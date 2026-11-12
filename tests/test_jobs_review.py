"""Standalone review tests for AutoStory autostory/jobs.py.

Scope: match_job(), Catalog.code(), assigned_document().
Strategy: import the read-only project package via sys.path and replace the
module-level read_json with synthetic metadata, so no MAA resource file,
network, ADB, emulator, Device or Core object is touched.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # never write .pyc into the project tree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autostory import jobs as jobsmod  # noqa: E402


# --------------------------------------------------------------------------
# synthetic metadata injected through jobs.read_json
# --------------------------------------------------------------------------
BATTLE_CHARS = {}
OVERVIEW_MAPS = []
STAGE_ROWS = []


def fake_read_json(path, default=None):
    p = str(path).replace("\\", "/")
    if p.endswith("resource/battle_data.json"):
        return {"chars": BATTLE_CHARS}
    if p.endswith("Arknights-Tile-Pos/overview.json"):
        return {str(i): m for i, m in enumerate(OVERVIEW_MAPS)}
    if p.endswith("resource/stages.json"):
        return list(STAGE_ROWS)
    raise AssertionError(f"unexpected read_json call in test: {path}")


def oper(oid, name, elite=2, level=1, potential=0, profession="WARRIOR", **extra):
    """One entry of MAA OperBox own_opers."""
    d = {
        "id": oid,
        "name": name,
        "own": True,
        "elite": elite,
        "level": level,
        "potential": potential,
        "profession": profession,
    }
    d.update(extra)
    return d


def req(name, elite=2, level=1, skill=3, **requirements):
    """One copilot operator requirement / group alternative."""
    r = {"elite": elite, "level": level}
    r.update(requirements)
    return {"name": name, "skill": skill, "requirements": r}


def box(*opers):
    return {"done": True, "own_opers": list(opers)}


def status(result):
    return result["status"]


class ReviewTest(unittest.TestCase):
    def setUp(self):
        global BATTLE_CHARS, OVERVIEW_MAPS, STAGE_ROWS
        BATTLE_CHARS = {}
        OVERVIEW_MAPS = []
        STAGE_ROWS = []
        self._orig_read_json = jobsmod.read_json
        jobsmod.read_json = fake_read_json

    def tearDown(self):
        jobsmod.read_json = self._orig_read_json

    # ------------------------------------------------------------------
    # assignment: uniqueness / backtracking
    # ------------------------------------------------------------------
    def test_same_operator_cannot_occupy_two_groups(self):
        """Two groups must never be filled by the same operator id."""
        b = box(oper("id-a", "A"), oper("id-b", "B"))
        doc = {
            "opers": [],
            "groups": [
                {"name": "g1", "opers": [req("A")]},
                {"name": "g2", "opers": [req("A"), req("B")]},
            ],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        ids = [entry["id"] for entry in r["assignment"]]
        self.assertEqual(len(ids), 2, r)
        self.assertEqual(len(set(ids)), 2, f"reused operator: {ids}")

    def test_same_operator_single_option_blocks(self):
        """If the only acceptable operator is needed by two groups, block."""
        b = box(oper("id-a", "A"))
        doc = {
            "opers": [],
            "groups": [
                {"name": "g1", "opers": [req("A")]},
                {"name": "g2", "opers": [req("A")]},
            ],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "blocked", r)
        self.assertEqual(r["assignment"], [])
        self.assertIn("争用同一个干员", r["reasons"][0], r)

    def test_backtracking_finds_alternate_assignment(self):
        """Greedy order {s2:C, s0:A} dead-ends at s1; solver must retry s0:B."""
        b = box(oper("id-a", "A"), oper("id-b", "B"), oper("id-c", "C"))
        doc = {
            "opers": [],
            "groups": [
                {"name": "s0", "opers": [req("A"), req("B")]},
                {"name": "s1", "opers": [req("A"), req("C")]},
                {"name": "s2", "opers": [req("C")]},
            ],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        by_slot = {entry["slot"]: entry["id"] for entry in r["assignment"]}
        self.assertEqual(by_slot, {"s0": "id-b", "s1": "id-a", "s2": "id-c"})
        self.assertEqual(len(set(by_slot.values())), 3)

    # ------------------------------------------------------------------
    # elite / level thresholds
    # ------------------------------------------------------------------
    def test_lower_elite_cannot_satisfy_higher_elite(self):
        b = box(oper("id-a", "A", elite=1, level=70))
        doc = {"opers": [req("A", elite=2, level=1, skill=1)], "groups": []}
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "blocked", r)
        self.assertIn("等级不足", r["reasons"][0], r)

    def test_higher_elite_low_level_satisfies_lower_elite_requirement(self):
        """E2L1 must satisfy an E1L55 requirement."""
        b = box(oper("id-a", "A", elite=2, level=1))
        doc = {"opers": [req("A", elite=1, level=55, skill=1)], "groups": []}
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        self.assertEqual(r["assignment"][0]["id"], "id-a")

    def test_same_elite_lower_level_blocked(self):
        b = box(oper("id-a", "A", elite=1, level=54))
        doc = {"opers": [req("A", elite=1, level=55, skill=1)], "groups": []}
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "blocked", r)
        self.assertIn("等级不足", r["reasons"][0], r)

    # ------------------------------------------------------------------
    # mastery / module evidence
    # ------------------------------------------------------------------
    def test_absent_mastery_is_unknown_not_match(self):
        """No mainSkillLevel in the box snapshot -> unknown, never a silent match."""
        b = box(oper("id-a", "A", skills=[{"level": 3}, {"level": 0}, {"level": 0}]))
        doc = {
            "opers": [req("A", elite=2, level=1, skill=3, skill_level=3)],
            "groups": [],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        self.assertIn("技能/专精待实测", r["assignment"][0]["unknown"])
        self.assertIn("技能/专精待实测", r["reasons"])

    def test_known_low_mastery_blocked(self):
        b = box(
            oper(
                "id-a",
                "A",
                mainSkillLevel=0,
                skills=[{"level": 3}, {"level": 0}, {"level": 0}],
            )
        )
        doc = {
            "opers": [req("A", elite=2, level=1, skill=3, skill_level=3)],
            "groups": [],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "blocked", r)
        self.assertIn("技能等级不足", r["reasons"][0], r)

    def test_module_absence_stays_unknown(self):
        """A module requirement can never be confirmed from box metadata here."""
        b = box(oper("id-a", "A"))
        doc = {
            "opers": [req("A", elite=2, level=1, skill=1, module=1, module_level=2)],
            "groups": [],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        unknown = r["assignment"][0]["unknown"]
        self.assertIn("模组待实测", unknown)
        self.assertIn("模组等级待实测", unknown)

    # ------------------------------------------------------------------
    # Catalog.code: exact aliases / raid variants
    # ------------------------------------------------------------------
    def test_catalog_exact_aliases(self):
        global OVERVIEW_MAPS, STAGE_ROWS
        OVERVIEW_MAPS = [
            {
                "stageId": "main_01-07",
                "levelId": "1-7",
                "code": "main_01-07",
                "name": "1-7",
            },
            {
                "stageId": "main_01-07#f#",
                "levelId": "1-7#f#",
                "code": "main_01-07#f#",
                "name": "1-7#f#",
            },
        ]
        STAGE_ROWS = [
            {"code": "main_01-07", "apCost": 6, "stageId": "main_01-07"},
            {"code": "main_01-07#f#", "apCost": 6, "stageId": "main_01-07#f#"},
        ]
        cat = jobsmod.Catalog()
        self.assertEqual(cat.code("1-7"), "main_01-07")
        self.assertEqual(cat.code("main_01-07"), "main_01-07")
        self.assertEqual(cat.code("1-7#f#"), "main_01-07#f#")
        self.assertEqual(cat.code("main_01-07#f#"), "main_01-07#f#")
        self.assertEqual(cat.code("NOPE-99"), None)

    def test_catalog_raid_variant_does_not_collapse_to_normal(self):
        global OVERVIEW_MAPS, STAGE_ROWS
        OVERVIEW_MAPS = [
            {"stageId": "main_01-07", "levelId": "1-7", "code": "main_01-07", "name": "1-7"},
            {
                "stageId": "main_01-07#f#",
                "levelId": "1-7#f#",
                "code": "main_01-07#f#",
                "name": "1-7#f#",
            },
        ]
        STAGE_ROWS = [
            {"code": "main_01-07", "apCost": 6, "stageId": "main_01-07"},
        ]
        cat = jobsmod.Catalog()
        normal = cat.code("1-7")
        raid = cat.code("1-7#f#")
        self.assertNotEqual(normal, raid)
        self.assertIn("#f#", raid)
        # A raid document must not be accepted for the normal stage.
        self.assertNotEqual(raid, normal)

    def test_catalog_ambiguous_alias_returns_none(self):
        global OVERVIEW_MAPS
        OVERVIEW_MAPS = [
            {"stageId": "a1", "levelId": "EV-1", "code": "a1", "name": "EV"},
            {"stageId": "a2", "levelId": "EV-2", "code": "a2", "name": "EV"},
        ]
        cat = jobsmod.Catalog()
        self.assertIsNone(cat.code("EV"))
        self.assertIsNone(cat.code(""))

    # ------------------------------------------------------------------
    # empty formations
    # ------------------------------------------------------------------
    def test_empty_groups_blocked(self):
        b = box(oper("id-a", "A"))
        r = jobsmod.match_job(
            {"opers": [], "groups": [{"name": "g1", "opers": []}]}, b
        )
        self.assertEqual(status(r), "blocked", r)
        self.assertTrue(r["reasons"][0].startswith("g1：没有符合已知条件的干员"), r)

    def test_no_slots_blocked(self):
        r = jobsmod.match_job({"opers": [], "groups": []}, box(oper("id-a", "A")))
        self.assertEqual(status(r), "blocked", r)
        self.assertIn("作业未声明编队", r["reasons"][0])

    def test_oper_slot_only_document(self):
        b = box(oper("id-a", "A"))
        doc = {"opers": [req("A", skill=1)], "groups": []}
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        self.assertEqual(r["assignment"][0]["slot"], "指定:0:A")

    # ------------------------------------------------------------------
    # document immutability
    # ------------------------------------------------------------------
    def test_match_job_does_not_mutate_document(self):
        b = box(oper("id-a", "A"), oper("id-b", "B"))
        doc = {
            "opers": [req("A", skill=1)],
            "groups": [{"name": "g1", "opers": [req("B", skill=1)]}],
        }
        snapshot = copy.deepcopy(doc)
        jobsmod.match_job(doc, b)
        self.assertEqual(doc, snapshot)

    def test_assigned_document_does_not_mutate_source(self):
        b = box(oper("id-a", "A"), oper("id-b", "B"))
        doc = {
            "opers": [{"name": "A", "skill": 3, "requirements": {"elite": 2, "level": 1}}],
            "groups": [
                {
                    "name": "g1",
                    "opers": [
                        {"name": "B", "skill": 1, "requirements": {"elite": 2, "level": 1}}
                    ],
                }
            ],
        }
        snapshot = copy.deepcopy(doc)
        r = jobsmod.match_job(doc, b)
        assigned = jobsmod.assigned_document(doc, r)
        self.assertEqual(doc, snapshot)
        self.assertIsNot(assigned, doc)
        self.assertEqual(assigned["opers"][0]["name"], "A")
        self.assertEqual(assigned["groups"][0]["opers"][0]["name"], "B")

    # ------------------------------------------------------------------
    # expected defect: the assigned document drops the required skill
    # ------------------------------------------------------------------
    def test_assigned_document_preserves_skill_selection(self):
        """The substituted operator must still carry the job's skill choice.

        A copilot operator entry carries `skill` / `skill_usage`; replacing the
        whole entry with MAA OperBox metadata loses them, so the assigned job
        can no longer select the skill the author required.
        """
        b = box(oper("id-a", "A"))
        doc = {
            "opers": [
                {
                    "name": "A",
                    "skill": 3,
                    "skill_usage": 2,
                    "requirements": {"elite": 2, "level": 1},
                }
            ],
            "groups": [],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        assigned = jobsmod.assigned_document(doc, r)
        entry = assigned["opers"][0]
        self.assertEqual(entry["name"], "A")
        self.assertEqual(entry.get("skill"), 3, f"skill choice lost: {entry}")
        self.assertEqual(entry.get("skill_usage"), 2, f"skill_usage lost: {entry}")


    # ------------------------------------------------------------------
    # expected defect: skill_level without skill is silently ignored
    # ------------------------------------------------------------------
    def test_skill_level_without_skill_is_unknown_not_matched(self):
        """An explicit skill_level demand must not be silently satisfied.

        The entry omits `skill` (copilot default is skill 1) but the author
        still requires mastery 3.  With no mastery evidence in the box the
        only safe outcome is "unknown", not unknown=[] (treated as matched).
        """
        b = box(
            oper(
                "id-a",
                "A",
                skills=[{"level": 3}, {"level": 0}, {"level": 0}],
            )
        )
        doc = {
            "opers": [
                {
                    "name": "A",
                    "requirements": {"elite": 2, "level": 1, "skill_level": 3},
                }
            ],
            "groups": [],
        }
        r = jobsmod.match_job(doc, b)
        self.assertEqual(status(r), "trial_required", r)
        self.assertIn(
            "技能/专精待实测",
            r["assignment"][0]["unknown"],
            f"explicit skill_level=3 was ignored: {r}",
        )

    # ------------------------------------------------------------------
    # property checks
    # ------------------------------------------------------------------
    def test_solver_matches_bruteforce_on_small_instances(self):
        """For random small squads, solvable <=> a perfect matching exists."""
        import random

        rng = random.Random(1234)
        b = box(*[oper(f"id-{i}", f"O{i}") for i in range(5)])

        def brute(adjacency, used, k):
            if k == len(adjacency):
                return True
            for oid in adjacency[k]:
                if oid in used:
                    continue
                used.add(oid)
                if brute(adjacency, used, k + 1):
                    return True
                used.remove(oid)
            return False

        checked = 0
        for _ in range(500):
            n_slots = rng.randint(1, 4)
            adjacency = []
            groups = []
            for s in range(n_slots):
                names = rng.sample([f"O{i}" for i in range(5)], rng.randint(1, 4))
                groups.append({"name": f"g{s}", "opers": [req(nm, skill=1) for nm in names]})
                adjacency.append([f"id-{int(nm[1:])}" for nm in names])
            feasible = brute(adjacency, set(), 0)
            r = jobsmod.match_job({"opers": [], "groups": groups}, b)
            if feasible:
                self.assertEqual(status(r), "trial_required", (groups, r))
                ids = [entry["id"] for entry in r["assignment"]]
                self.assertEqual(len(set(ids)), n_slots, (groups, r))
                for slot, entry in enumerate(r["assignment"]):
                    self.assertIn(entry["id"], adjacency[slot], (groups, r))
            else:
                self.assertEqual(status(r), "blocked", (groups, r))
            checked += 1
        self.assertEqual(checked, 500)

    def test_catalog_alias_consistency_property(self):
        """Every alias key resolves to a code, or to None when it is ambiguous."""
        import random

        global OVERVIEW_MAPS
        rng = random.Random(99)
        maps = []
        for i in range(30):
            maps.append(
                {
                    "stageId": f"S{rng.randint(0, 9)}",
                    "levelId": f"L{rng.randint(0, 9)}",
                    "code": f"code-{i}",
                    "name": f"N{rng.randint(0, 9)}",
                }
            )
        OVERVIEW_MAPS = maps
        cat = jobsmod.Catalog()
        for key in ("stageId", "levelId", "code", "name"):
            for m in maps:
                expected = {x["code"] for x in maps if x.get(key) == m[key]}
                got = cat.code(m[key])
                if len(expected) == 1:
                    self.assertEqual(got, next(iter(expected)), (key, m[key]))
                else:
                    self.assertIsNone(got, (key, m[key], expected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
