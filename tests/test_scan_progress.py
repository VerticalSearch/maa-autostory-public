"""stdlib unittest coverage for the pure scan_progress module."""

import copy
import os
import sys
import unittest

from autostory import scan_progress as sp


def stage(code, mode, group, **extra):
    value = {
        "code": code,
        "mode": mode,
        "group": group,
        "chapter": group,
        "name": code,
    }
    value.update(extra)
    return value


def record(code, mode, state="cleared", evidence="C:/screens/x-detail.png",
           at="2026-10-31T12:00:00+08:00", **extra):
    value = {
        "code": code,
        "mode": mode,
        "state": state,
        "evidence": evidence,
        "at": at,
    }
    value.update(extra)
    return value


def make_book(stages, profiles=None):
    if profiles is None:
        groups = []
        for value in stages.values():
            if value["group"] not in groups:
                groups.append(value["group"])
        profiles = {group: {"title": group, "codes": []} for group in groups}
    return {"profiles": profiles, "stages": dict(stages)}


class SummarizeGroupTests(unittest.TestCase):
    def test_normal_and_raid_are_independently_required(self):
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1"),
            "A-1:raid": stage("A-1", "raid", "G1"),
        })
        progress = {"A-1:normal": record("A-1", "normal")}
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["confirmed"], 1)
        self.assertEqual(summary["cleared"], 1)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["missing_keys"], ["A-1:raid"])
        self.assertFalse(summary["complete"])
        self.assertEqual(sp.plan_groups(book, progress), ["G1"])

        # A raid clear does not confirm the normal stage either.
        raid_only = {"A-1:raid": record("A-1", "raid")}
        summary = sp.summarize_group(book, raid_only, "G1")
        self.assertEqual(summary["missing_keys"], ["A-1:normal"])
        self.assertFalse(summary["complete"])

    def test_all_known_uncleared_counts_as_scanned_and_complete(self):
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1"),
            "A-1:raid": stage("A-1", "raid", "G1"),
        })
        progress = {
            "A-1:normal": record("A-1", "normal", "uncleared"),
            "A-1:raid": record("A-1", "raid", "partial"),
        }
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["confirmed"], 2)
        self.assertEqual(summary["cleared"], 0)
        self.assertEqual(summary["pending"], 2)
        self.assertEqual(summary["pending_keys"], ["A-1:normal", "A-1:raid"])
        self.assertTrue(summary["complete"])
        # Scan-only does not fight confirmed unmet stages.
        self.assertEqual(sp.plan_groups(book, progress), [])

    def test_stale_wrong_ids_modes_and_unknown_state_do_not_confirm(self):
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1"),
            "A-2:normal": stage("A-2", "normal", "G1"),
            "A-3:normal": stage("A-3", "normal", "G1"),
            "A-4:normal": stage("A-4", "normal", "G1"),
            "A-5:normal": stage("A-5", "normal", "G1"),
        })
        progress = {
            "A-1:normal": record("A-9", "normal"),          # wrong code
            "A-2:normal": record("A-2", "raid"),            # wrong mode
            "A-3:normal": record("A-3", "normal", "unknown"),
            "A-4": record("A-4", "normal"),                 # wrong key
            "A-5:normal": record("A-5", "normal", "unseen"),
            "ZZZ:normal": record("ZZZ", "normal"),          # unrelated stale
        }
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["confirmed"], 0)
        self.assertEqual(summary["missing_keys"],
                         ["A-1:normal", "A-2:normal", "A-3:normal",
                          "A-4:normal", "A-5:normal"])
        self.assertFalse(summary["complete"])

    def test_nonempty_evidence_and_at_are_required(self):
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1"),
            "A-2:normal": stage("A-2", "normal", "G1"),
            "A-3:normal": stage("A-3", "normal", "G1"),
            "A-4:normal": stage("A-4", "normal", "G1"),
            "A-5:normal": stage("A-5", "normal", "G1"),
            "A-6:normal": stage("A-6", "normal", "G1"),
        })
        progress = {
            "A-1:normal": record("A-1", "normal", evidence=None),
            "A-2:normal": record("A-2", "normal", evidence=""),
            "A-3:normal": record("A-3", "normal", evidence="   "),
            "A-4:normal": record("A-4", "normal", at=None),
            "A-5:normal": record("A-5", "normal", at=""),
            "A-6:normal": record("A-6", "normal", at=1234567890),
        }
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["confirmed"], 0)
        self.assertEqual(summary["total"], 6)
        self.assertFalse(summary["complete"])

    def test_locked_is_confirmed_but_not_pending(self):
        book = make_book({"A-1:normal": stage("A-1", "normal", "G1")})
        progress = {"A-1:normal": record("A-1", "normal", "locked")}
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["confirmed"], 1)
        self.assertEqual(summary["cleared"], 0)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["pending_keys"], [])
        self.assertEqual(summary["locked_keys"], ["A-1:normal"])
        self.assertTrue(summary["complete"])
        self.assertEqual(sp.plan_groups(book, progress), [])

    def test_unsupported_mode_is_separate_and_prevents_complete(self):
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1"),
            "A-2:easy": stage("A-2", "easy", "G1"),
            "B-1:six_star": stage("B-1", "six_star", "G2"),
        })
        progress = {
            "A-1:normal": record("A-1", "normal"),
            # Unsupported keys are never confirmable even with evidence.
            "A-2:easy": record("A-2", "easy"),
            "B-1:six_star": record("B-1", "six_star"),
        }
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["confirmed"], 1)
        self.assertEqual(summary["unsupported_keys"], ["A-2:easy"])
        self.assertFalse(summary["complete"])
        # Scan-only cannot resolve unsupported modes, so it is not scan work.
        self.assertEqual(sp.plan_groups(book, progress), [])

        only_unsupported = sp.summarize_group(book, progress, "G2")
        self.assertEqual(only_unsupported["total"], 0)
        self.assertEqual(only_unsupported["unsupported_keys"], ["B-1:six_star"])
        self.assertFalse(only_unsupported["complete"])

    def test_group_match_is_exact_not_chapter_or_parent_state(self):
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1", chapter="G2"),
            "B-1:normal": stage("B-1", "normal", "G2", chapter="G1"),
            "TR-1:normal": stage("TR-1", "normal", "G1", name="训练"),
        })
        progress = {"A-1:normal": record("A-1", "normal")}
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["missing_keys"], ["TR-1:normal"])
        self.assertFalse(summary["complete"])

        # A cleared parent normal key cannot confirm a missing raid key.
        book = make_book({
            "A-1:normal": stage("A-1", "normal", "G1"),
            "A-1:raid": stage("A-1", "raid", "G1"),
        })
        summary = sp.summarize_group(
            book, {"A-1:normal": record("A-1", "normal")}, "G1")
        self.assertEqual(summary["missing_keys"], ["A-1:raid"])

    def test_keys_are_sorted_deterministically(self):
        book = make_book({
            "C:normal": stage("C", "normal", "G1"),
            "A:normal": stage("A", "normal", "G1"),
            "B:normal": stage("B", "normal", "G1"),
            "D:easy": stage("D", "easy", "G1"),
            "E:normal": stage("E", "normal", "G1"),
            "F:normal": stage("F", "normal", "G1"),
        })
        progress = {
            "C:normal": record("C", "normal", "uncleared"),
            "B:normal": record("B", "normal", "partial"),
        }
        summary = sp.summarize_group(book, progress, "G1")
        self.assertEqual(summary["missing_keys"], ["A:normal", "E:normal", "F:normal"])
        self.assertEqual(summary["pending_keys"], ["B:normal", "C:normal"])
        self.assertEqual(summary["unsupported_keys"], ["D:easy"])

    def test_unknown_empty_and_malformed_groups_are_not_complete(self):
        book = make_book({"A-1:normal": stage("A-1", "normal", "G1")})
        for group in ("NOPE", "", None):
            summary = sp.summarize_group(book, {}, group)
            self.assertEqual(summary["group"], group)
            self.assertEqual(summary["total"], 0)
            self.assertEqual(summary["confirmed"], 0)
            self.assertEqual(summary["missing_keys"], [])
            self.assertFalse(summary["complete"])
        self.assertEqual(sp.summarize_group({}, {}, "G1")["total"], 0)
        self.assertFalse(sp.summarize_group({}, {}, "G1")["complete"])


class SummarizeScanTests(unittest.TestCase):
    def test_counts_only_known_keys_and_tracks_unseen(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "B:normal": stage("B", "normal", "G1"),
            "C:normal": stage("C", "normal", "G1"),
            "D:normal": stage("D", "normal", "G1"),
            "E:normal": stage("E", "normal", "G2"),
        }, profiles={"G2": {}, "G1": {}})
        progress = {
            "A:normal": record("A", "normal"),
            "B:normal": record("B", "normal", "uncleared"),
            "D:normal": record("D", "normal", "unseen"),
            "STALE:normal": record("STALE", "normal"),
            "E:normal": record("E", "normal", "locked"),
            "G2-OLD": record("OLD", "normal"),
        }
        scan = sp.summarize_scan(book, progress)
        self.assertEqual(scan["total"], 5)
        self.assertEqual(scan["confirmed"], 3)
        self.assertEqual(scan["cleared"], 1)
        self.assertEqual(scan["pending"], 1)
        self.assertEqual(scan["unseen"], 2)  # C is absent, D is unseen
        self.assertEqual(scan["unsupported"], 0)
        self.assertFalse(scan["complete"])
        self.assertEqual(set(scan["groups"]), {"G1", "G2"})
        self.assertEqual(scan["groups"]["G1"]["total"], 4)
        self.assertEqual(scan["groups"]["G2"]["confirmed"], 1)

    def test_empty_catalogue_or_progress_cannot_be_complete(self):
        self.assertEqual(sp.summarize_scan({}, {}), {
            "total": 0, "confirmed": 0, "cleared": 0, "pending": 0,
            "unseen": 0, "unsupported": 0, "complete": False, "groups": {},
        })
        book = {"profiles": {"G1": {}}, "stages": {}}
        scan = sp.summarize_scan(book, {"A:normal": record("A", "normal")})
        self.assertEqual(scan["total"], 0)
        self.assertEqual(scan["confirmed"], 0)
        self.assertFalse(scan["complete"])

    def test_unsupported_count_and_truthful_complete(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "A:easy": stage("A", "easy", "G1"),
            "B:normal": stage("B", "normal", "G2"),
        })
        progress = {
            "A:normal": record("A", "normal"),
            "A:easy": record("A", "easy"),
            "B:normal": record("B", "normal"),
        }
        scan = sp.summarize_scan(book, progress)
        self.assertEqual(scan["total"], 2)
        self.assertEqual(scan["confirmed"], 2)
        self.assertEqual(scan["unsupported"], 1)
        self.assertFalse(scan["complete"])

        book["stages"].pop("A:easy")
        scan = sp.summarize_scan(book, progress)
        self.assertEqual(scan["unsupported"], 0)
        self.assertTrue(scan["complete"])

    def test_deleted_chapter_progress_is_ignored_without_errors(self):
        book = make_book({"A:normal": stage("A", "normal", "G1")},
                         profiles={"G1": {}})
        progress = {
            "A:normal": record("A", "normal"),
            "B:normal": record("B", "normal"),  # deleted chapter G2 remains stale
        }
        scan = sp.summarize_scan(book, progress)
        self.assertEqual(scan["total"], 1)
        self.assertEqual(scan["confirmed"], 1)
        self.assertEqual(set(scan["groups"]), {"G1"})
        self.assertTrue(scan["complete"])
        self.assertEqual(sp.plan_groups(book, progress), [])
        self.assertEqual(sp.summarize_group(book, progress, "G2")["total"], 0)

    def test_duplicate_unrelated_progress_is_ignored(self):
        book = make_book({"A:normal": stage("A", "normal", "G1")})
        progress = {
            "A:normal": record("A", "normal"),
            "A:normal:old": record("A", "normal"),
            "A": record("A", "normal"),
            "UNRELATED": {"state": "cleared"},
        }
        scan = sp.summarize_scan(book, progress)
        self.assertEqual(scan["total"], 1)
        self.assertEqual(scan["confirmed"], 1)
        self.assertTrue(scan["complete"])


class PlanGroupsTests(unittest.TestCase):
    def test_partial_first_group_stays_first_on_resume(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "B:normal": stage("B", "normal", "G2"),
            "C:normal": stage("C", "normal", "G3"),
        }, profiles={"G1": {}, "G2": {}, "G3": {}})
        progress = {"A:normal": record("A", "normal")}  # G1 is fully confirmed
        self.assertEqual(sp.plan_groups(book, progress), ["G2", "G3"])

        # Make G1 partial again: it is the active group and must be first.
        progress = {}
        self.assertEqual(
            sp.plan_groups(book, progress, {"active_group": "G1"}),
            ["G1", "G2", "G3"],
        )
        self.assertEqual(
            sp.plan_groups(book, progress, {"active_group": "G2"}),
            ["G2", "G1", "G3"],
        )

    def test_catalogue_profile_order_is_retained(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G3"),
            "B:normal": stage("B", "normal", "G1"),
            "C:normal": stage("C", "normal", "G2"),
        }, profiles={"G3": {}, "G1": {}, "G2": {}})
        self.assertEqual(sp.plan_groups(book, {}), ["G3", "G1", "G2"])
        self.assertEqual(
            sp.plan_groups(book, {}, {"active_group": "G2"}),
            ["G2", "G3", "G1"],
        )

    def test_complete_active_group_is_not_moved_and_is_skipped(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "B:normal": stage("B", "normal", "G2"),
            "C:normal": stage("C", "normal", "G3"),
        }, profiles={"G1": {}, "G2": {}, "G3": {}})
        progress = {"C:normal": record("C", "normal")}
        self.assertEqual(
            sp.plan_groups(book, progress, {"active_group": "G3"}),
            ["G1", "G2"],
        )

    def test_deleted_or_removed_group_in_previous_is_safe(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "B:normal": stage("B", "normal", "G2"),
        }, profiles={"G1": {}, "G2": {}})
        previous = {"active_group": "REMOVED", "reports": {"G1": "done"}}
        self.assertEqual(sp.plan_groups(book, {}, previous), ["G1", "G2"])
        self.assertEqual(sp.summarize_group(book, {}, "REMOVED")["total"], 0)

    def test_malformed_previous_is_handled_safely(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "B:normal": stage("B", "normal", "G2"),
        }, profiles={"G1": {}, "G2": {}})
        for previous in (None, {}, "bad", 42, [], {"active_group": None},
                         {"active_group": ""}, {"active_group": ["G1"]},
                         {"active_group": {"name": "G1"}}):
            self.assertEqual(sp.plan_groups(book, {}, previous), ["G1", "G2"])

    def test_old_reports_do_not_skip_incomplete_groups(self):
        book = make_book({
            "A:normal": stage("A", "normal", "G1"),
            "C:normal": stage("C", "normal", "G1"),
            "B:normal": stage("B", "normal", "G2"),
        }, profiles={"G1": {}, "G2": {}})
        progress = {"A:normal": record("A", "normal", "uncleared")}
        previous = {
            "active_group": "G1",
            "reports": {"G2": {"confirmed": 99}},
            "complete": ["G2"],
        }
        self.assertEqual(sp.plan_groups(book, progress, previous), ["G1", "G2"])

    def test_empty_or_unknown_catalogue_plans_nothing(self):
        self.assertEqual(sp.plan_groups({}, {}), [])
        self.assertEqual(sp.plan_groups({"profiles": {}, "stages": {}}, {}), [])
        self.assertEqual(
            sp.plan_groups({"profiles": {"G1": {}}, "stages": {}}, {}), [])


class ImmutabilityTests(unittest.TestCase):
    def test_inputs_are_not_mutated(self):
        book = make_book({
            "C:normal": stage("C", "normal", "G2"),
            "A:normal": stage("A", "normal", "G1"),
            "A:raid": stage("A", "raid", "G1"),
            "B:easy": stage("B", "easy", "G2"),
        }, profiles={"G1": {}, "G2": {}})
        progress = {
            "A:normal": record("A", "normal"),
            "C:normal": record("C", "normal", "uncleared"),
        }
        previous = {"active_group": "G2", "reports": {"G1": "done"}}
        book_before = copy.deepcopy(book)
        progress_before = copy.deepcopy(progress)
        previous_before = copy.deepcopy(previous)

        sp.summarize_group(book, progress, "G1")
        sp.summarize_group(book, progress, "G2")
        sp.summarize_scan(book, progress)
        sp.plan_groups(book, progress, previous)

        self.assertEqual(book, book_before)
        self.assertEqual(progress, progress_before)
        self.assertEqual(previous, previous_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
