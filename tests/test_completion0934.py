"""Isolated unit tests for completion.summarize_execution.

Only the candidate module is imported; no production AutoStory module, device,
filesystem state or network is touched.
"""

import copy
import unittest

from autostory import completion


def stage(code, mode, group):
    return {"code": code, "mode": mode, "group": group}


def make_book():
    return {
        "profiles": {"A": {"title": "A"}, "B": {"title": "B"}},
        "stages": {
            "A-1:normal": stage("A-1", "normal", "A"),
            "A-1:raid": stage("A-1", "raid", "A"),
            "B-1:normal": stage("B-1", "normal", "B"),
        },
    }


def make_ledger(**overrides):
    value = {
        "sanity": 100,
        "count": 1,
        "limit": 10,
        "practice_count": 1,
        "practice_limit": 5,
    }
    value.update(overrides)
    return value


class CompletionTests(unittest.TestCase):
    def test_all_known_complete(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
            "B-1:normal": {"state": "cleared"},
        }
        out = completion.summarize_execution(book, progress, ["A", "B"], make_ledger())
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"], {
            "total": 3, "cleared": 3, "pending": 0, "locked": 0,
            "unknown": 0, "unsupported": 0, "blocked_groups": 0, "skipped": 0,
        })
        self.assertEqual(out["message"], "普通/突袭范围完成。")

    def test_missing_records_are_unknown_not_cleared(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "cleared"}}
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["total"], 2)
        self.assertEqual(out["summary"]["cleared"], 1)
        self.assertEqual(out["summary"]["unknown"], 1)
        self.assertIn("待确认 1", out["message"])

    def test_pending_locked_and_unknown_are_counted_separately(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:normal": stage("A-1", "normal", "A"),
                "A-2:normal": stage("A-2", "normal", "A"),
                "A-3:normal": stage("A-3", "normal", "A"),
                "A-4:normal": stage("A-4", "normal", "A"),
            },
        }
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-2:normal": {"state": "uncleared"},
            "A-3:normal": {"state": "locked"},
        }
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["cleared"], 1)
        self.assertEqual(out["summary"]["pending"], 1)
        self.assertEqual(out["summary"]["locked"], 1)
        self.assertEqual(out["summary"]["unknown"], 1)
        for word in ("已通关 1", "待确认 1", "未通关/未满星 1", "锁定 1"):
            self.assertIn(word, out["message"])

    def test_partial_state_is_pending_not_cleared(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "partial"}}
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["pending"], 1)
        self.assertEqual(out["summary"]["cleared"], 0)
        self.assertEqual(out["summary"]["unknown"], 1)

    def test_unseen_and_unknown_states_are_unconfirmed_not_malformed(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:normal": stage("A-1", "normal", "A"),
                "A-2:normal": stage("A-2", "normal", "A"),
            },
        }
        progress = {
            "A-1:normal": {"state": "unseen"},
            "A-2:normal": {"state": "unknown"},
        }
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["unknown"], 2)
        self.assertNotIn("进度记录无效", out["message"])

    def test_unrecognised_state_is_malformed_and_fails_closed(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "mystery"}}
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(sanity=0))
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["unknown"], 2)
        self.assertIn("进度记录无效", out["message"])
        self.assertNotEqual(out["state"], "budget_exhausted")

    def test_record_code_mismatch_is_malformed(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "cleared", "code": "OTHER"}}
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["cleared"], 0)
        self.assertIn("进度记录无效", out["message"])

    def test_special_difficulties_are_separate_and_mentioned_on_done(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:normal": stage("A-1", "normal", "A"),
                "A-1:easy": stage("A-1", "easy", "A"),
                "A-1:six_star": stage("A-1", "six_star", "A"),
            },
        }
        progress = {"A-1:normal": {"state": "cleared"}}
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"]["total"], 1)
        self.assertEqual(out["summary"]["cleared"], 1)
        self.assertEqual(out["summary"]["unsupported"], 2)
        self.assertIn("普通/突袭范围完成", out["message"])
        self.assertIn("另有 2 个特殊难度未验证", out["message"])
        self.assertNotIn("不代表", out["message"])

    def test_group_with_only_special_difficulties_is_partial(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:easy": stage("A-1", "easy", "A"),
                "A-1:tough": stage("A-1", "tough", "A"),
            },
        }
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(sanity=0))
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["total"], 0)
        self.assertEqual(out["summary"]["unsupported"], 2)
        self.assertIn("没有普通/突袭关卡", out["message"])

    def test_empty_groups_is_partial_not_vacuous_done(self):
        out = completion.summarize_execution(make_book(), {}, [], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["total"], 0)
        self.assertIn("未选择任何分组", out["message"])
        self.assertNotIn("范围完成", out["message"])

    def test_empty_groups_with_no_budget_stays_partial(self):
        out = completion.summarize_execution(
            make_book(), {}, [], make_ledger(sanity=0, count=0, limit=0))
        self.assertEqual(out["state"], "partial")

    def test_unknown_group_is_explained_and_prevents_done(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        out = completion.summarize_execution(book, progress, ["A", "Z"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertIn("Z", out["message"])
        self.assertIn("不在目录中", out["message"])
        self.assertEqual(out["summary"]["total"], 2)
        self.assertEqual(out["summary"]["cleared"], 2)

    def test_only_unknown_group_is_partial(self):
        out = completion.summarize_execution(make_book(), {}, ["Z"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["total"], 0)
        self.assertIn("Z", out["message"])

    def test_invalid_groups_container_is_partial(self):
        for groups in ("A", None, {"A"}, 7):
            with self.subTest(groups=groups):
                out = completion.summarize_execution(
                    make_book(), {}, groups, make_ledger())
                self.assertEqual(out["state"], "partial")
                self.assertIn("分组列表无效", out["message"])

    def test_invalid_group_entry_is_partial_but_valid_entries_count(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        out = completion.summarize_execution(
            book, progress, ["A", None, ""], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["total"], 2)
        self.assertEqual(out["summary"]["cleared"], 2)

    def test_sanity_exhaustion(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "cleared"}}
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(sanity=0))
        self.assertEqual(out["state"], "budget_exhausted")
        self.assertIn("sanity=0", out["reason"])
        self.assertIn("本轮理智预算已用完", out["message"])
        self.assertIn("尚余 1 项", out["message"])
        self.assertNotIn("sanity", out["message"])
        self.assertNotIn("不代表", out["message"])

    def test_negative_sanity_is_exhausted(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(sanity=-3))
        self.assertEqual(out["state"], "budget_exhausted")
        self.assertIn("sanity=-3", out["reason"])

    def test_formal_limit_exhaustion(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(sanity=50, count=2, limit=2))
        self.assertEqual(out["state"], "budget_exhausted")
        self.assertIn("正式作战次数已达上限", out["reason"])
        self.assertIn("本轮正式作战次数已达上限", out["message"])
        self.assertNotIn("count=", out["message"])

    def test_zero_formal_limit_is_exhausted(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(sanity=50, count=0, limit=0))
        self.assertEqual(out["state"], "budget_exhausted")

    def test_formal_limit_none_is_unlimited(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(sanity=50, count=999, limit=None))
        self.assertEqual(out["state"], "partial")
        self.assertNotEqual(out["state"], "budget_exhausted")

    def test_practice_limit_exhaustion(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"],
            make_ledger(sanity=50, count=1, limit=10,
                        practice_count=3, practice_limit=3))
        self.assertEqual(out["state"], "budget_exhausted")
        self.assertIn("演习次数已达上限", out["reason"])
        self.assertIn("本轮演习次数已达上限", out["message"])
        self.assertNotIn("practice_count", out["message"])

    def test_practice_limit_none_is_unlimited(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"],
            make_ledger(sanity=50, count=1, limit=10,
                        practice_count=999, practice_limit=None))
        self.assertEqual(out["state"], "partial")

    def test_budget_message_uses_player_facing_limits_without_fields(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"],
            make_ledger(sanity=0, count=2, limit=2,
                        practice_count=3, practice_limit=3))
        self.assertEqual(out["state"], "budget_exhausted")
        message = out["message"]
        for text in (
            "本轮理智预算已用完",
            "正式作战次数已达上限",
            "演习次数已达上限",
            "尚余 2 项",
        ):
            self.assertIn(text, message)
        for text in ("sanity", "count=", "limit=", "practice_count", "不代表"):
            self.assertNotIn(text, message)

    def test_budget_exhaustion_does_not_undo_done(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        out = completion.summarize_execution(
            book, progress, ["A"],
            make_ledger(sanity=0, count=0, limit=0,
                        practice_count=0, practice_limit=0))
        self.assertEqual(out["state"], "done")
        self.assertIn("普通/突袭范围完成", out["message"])

    def test_done_with_special_modes_and_no_budget_stays_done(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:normal": stage("A-1", "normal", "A"),
                "A-1:tough": stage("A-1", "tough", "A"),
            },
        }
        out = completion.summarize_execution(
            book, {"A-1:normal": {"state": "cleared"}}, ["A"],
            make_ledger(sanity=0))
        self.assertEqual(out["state"], "done")
        self.assertIn("特殊难度", out["message"])

    def test_remaining_budget_means_partial_not_exhausted(self):
        book = make_book()
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(sanity=50, count=1, limit=10,
                                         practice_count=1, practice_limit=5))
        self.assertEqual(out["state"], "partial")
        self.assertIn("未完成", out["message"])

    def test_last_group_report_wins_clearing_a_block(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        reports = [
            {"group": "A", "blocked": "navigation failed"},
            {"group": "A", "outcomes": []},
        ]
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(), reports)
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"]["blocked_groups"], 0)

    def test_last_group_report_wins_adding_a_block(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        reports = [
            {"group": "A", "outcomes": []},
            {"group": "A", "blocked": "scan failed"},
        ]
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(), reports)
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["blocked_groups"], 1)
        self.assertIn("最近报告阻塞", out["message"])

    def test_blocking_report_prevents_done_even_when_cleared(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(),
            [{"group": "A", "blocked": "terminal outage"}])
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["blocked_groups"], 1)

    def test_reports_for_unrelated_groups_are_ignored(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        reports = [
            {
                "group": "Z",
                "blocked": "old unrelated failure",
                "outcomes": [
                    {"code": "Z-9", "mode": "normal", "result": "skipped"},
                ],
            },
            {"group": "Z", "outcomes": "malformed unrelated report"},
        ]
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(), reports)
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"]["blocked_groups"], 0)
        self.assertEqual(out["summary"]["skipped"], 0)

    def test_skipped_outside_requested_scope_is_ignored(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:normal": stage("A-1", "normal", "A"),
                "A-1:easy": stage("A-1", "easy", "A"),
            },
        }
        reports = [{
            "group": "A",
            "outcomes": [
                {"code": "Z-9", "mode": "normal", "result": "skipped"},
                {"code": "A-1", "mode": "easy", "result": "skipped"},
            ],
        }]
        out = completion.summarize_execution(
            book, {"A-1:normal": {"state": "cleared"}}, ["A"],
            make_ledger(), reports)
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"]["unsupported"], 1)
        self.assertEqual(out["summary"]["skipped"], 0)

    def test_stale_skipped_on_now_cleared_stage_is_ignored(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        reports = [{
            "group": "A",
            "outcomes": [
                {"code": "A-1", "mode": "normal", "result": "skipped"},
                {"code": "A-1", "mode": "raid", "result": "skipped"},
            ],
        }]
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(), reports)
        self.assertEqual(out["summary"]["skipped"], 0)

    def test_only_non_cleared_skipped_outcomes_count(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "cleared"}}
        reports = [{
            "group": "A",
            "outcomes": [
                {"code": "A-1", "mode": "normal", "result": "skipped"},
                {"code": "A-1", "mode": "raid", "result": "skipped"},
            ],
        }]
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(), reports)
        self.assertEqual(out["summary"]["skipped"], 1)
        self.assertIn("1 个关卡被跳过", out["message"])

    def test_latest_report_drops_older_skipped_outcomes(self):
        book = make_book()
        reports = [
            {"group": "A", "outcomes": [
                {"code": "A-1", "mode": "normal", "result": "skipped"},
            ]},
            {"group": "A", "outcomes": []},
        ]
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(), reports)
        self.assertEqual(out["summary"]["skipped"], 0)
        self.assertEqual(out["state"], "partial")

    def test_skipped_counts_unique_stage_mode_across_latest_reports(self):
        book = make_book()
        reports = [
            {"group": "A", "outcomes": [
                {"code": "A-1", "mode": "normal", "result": "skipped"},
                {"code": "A-1", "mode": "normal", "result": "skipped"},
            ]},
            {"group": "B", "outcomes": [
                {"code": "A-1", "mode": "normal", "result": "skipped"},
            ]},
        ]
        out = completion.summarize_execution(
            book, {}, ["A", "B"], make_ledger(), reports)
        self.assertEqual(out["summary"]["skipped"], 1)

    def test_reports_do_not_upgrade_progress_to_cleared(self):
        book = make_book()
        reports = [{
            "group": "A",
            "outcomes": [
                {"code": "A-1", "mode": "normal", "result": "cleared"},
                {"code": "A-1", "mode": "raid", "result": "passed"},
            ],
        }]
        out = completion.summarize_execution(
            book, {}, ["A"], make_ledger(), reports)
        self.assertEqual(out["summary"]["cleared"], 0)
        self.assertEqual(out["state"], "partial")

    def test_progress_not_mapping_fails_closed(self):
        out = completion.summarize_execution(
            make_book(), None, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["unknown"], 2)
        self.assertIn("进度记录无效", out["message"])

    def test_malformed_progress_record_fails_closed(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "cleared"}, "A-1:raid": None}
        out = completion.summarize_execution(book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertIn("进度记录无效", out["message"])

    def test_mixed_validity_is_not_sticky_in_either_stage_order(self):
        progress = {
            "A-1:normal": {"state": "mystery"},
            "A-2:normal": {"state": "cleared"},
            "A-3:normal": {"state": "partial"},
        }
        for order in (("A-1", "A-2", "A-3"), ("A-3", "A-2", "A-1")):
            book = {
                "profiles": {"A": {}},
                "stages": {
                    code + ":normal": stage(code, "normal", "A")
                    for code in order
                },
            }
            with self.subTest(order=order):
                out = completion.summarize_execution(
                    book, progress, ["A"], make_ledger())
                self.assertEqual(out["state"], "partial")
                self.assertEqual(out["summary"]["total"], 3)
                self.assertEqual(out["summary"]["cleared"], 1)
                self.assertEqual(out["summary"]["pending"], 1)
                self.assertEqual(out["summary"]["unknown"], 1)
                self.assertIn("进度记录无效", out["message"])

    def test_invalid_ledger_fields_fail_closed(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        bad_ledgers = [
            None,
            {},
            {"sanity": "100", "count": 1, "limit": 10,
             "practice_count": 1, "practice_limit": 5},
            {"sanity": 100, "count": -1, "limit": 10,
             "practice_count": 1, "practice_limit": 5},
            {"sanity": 100, "count": 1, "limit": -1,
             "practice_count": 1, "practice_limit": 5},
            {"sanity": 100, "count": 1, "limit": 10,
             "practice_count": 1.5, "practice_limit": 5},
            {"sanity": 100, "count": 1, "limit": 10,
             "practice_count": 1, "practice_limit": -1},
            {"sanity": 100, "count": 1, "limit": True,
             "practice_count": 1, "practice_limit": 5},
        ]
        for ledger in bad_ledgers:
            with self.subTest(ledger=ledger):
                out = completion.summarize_execution(
                    book, progress, ["A"], ledger)
                self.assertEqual(out["state"], "partial")
                self.assertIn("预算记录无效", out["message"])
                self.assertNotIn("sanity", out["message"])
                self.assertIn("账本无效", out["reason"])

    def test_invalid_book_fails_closed(self):
        for book in (None, {}, {"stages": None}, {"stages": []}):
            with self.subTest(book=book):
                out = completion.summarize_execution(
                    book, {}, ["A"], make_ledger())
                self.assertEqual(out["state"], "partial")
                self.assertIn("目录数据无效", out["message"])

    def test_malformed_stage_entry_fails_closed(self):
        book = {
            "profiles": {"A": {}},
            "stages": {
                "A-1:normal": {"group": "A", "mode": "normal"},
                "A-2:normal": "not-a-stage",
            },
        }
        out = completion.summarize_execution(
            book, {"A-1:normal": {"state": "cleared"}}, ["A"], make_ledger())
        self.assertEqual(out["state"], "partial")
        self.assertEqual(out["summary"]["total"], 1)
        self.assertEqual(out["summary"]["cleared"], 1)

    def test_malformed_superseded_report_does_not_poison_latest(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        reports = [
            {"group": "A", "outcomes": "malformed but superseded"},
            {"group": "A", "outcomes": []},
        ]
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger(), reports)
        self.assertEqual(out["state"], "done")
        self.assertNotIn("分组报告无效", out["message"])

    def test_invalid_reports_shape_fails_closed(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        for reports in (
            {"A": {}},
            [{"group": "A", "outcomes": "x"}],
            [{"group": "A", "outcomes": [None]}],
            [
                {"group": "A", "outcomes": []},
                {"group": "A", "outcomes": "latest is malformed"},
            ],
        ):
            with self.subTest(reports=reports):
                out = completion.summarize_execution(
                    book, progress, ["A"], make_ledger(), reports)
                self.assertEqual(out["state"], "partial")
                self.assertIn("分组报告无效", out["message"])

    def test_duplicate_requested_groups_are_not_double_counted(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        out = completion.summarize_execution(
            book, progress, ["A", "A"], make_ledger())
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"]["total"], 2)
        self.assertEqual(out["summary"]["cleared"], 2)

    def test_progress_outside_requested_scope_is_ignored(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
            "B-1:normal": {"state": "uncleared"},
        }
        out = completion.summarize_execution(
            book, progress, ["A"], make_ledger())
        self.assertEqual(out["state"], "done")
        self.assertEqual(out["summary"]["total"], 2)
        self.assertEqual(out["summary"]["pending"], 0)

    def test_inputs_are_not_mutated(self):
        book = make_book()
        progress = {"A-1:normal": {"state": "cleared"}}
        groups = ["A", "A"]
        ledger = make_ledger(sanity=0)
        reports = [
            {"group": "A", "blocked": "old", "outcomes": [
                {"code": "A-1", "mode": "raid", "result": "skipped"},
            ]},
            {"group": "B", "outcomes": []},
        ]
        before = copy.deepcopy((book, progress, groups, ledger, reports))
        completion.summarize_execution(book, progress, groups, ledger, reports)
        self.assertEqual((book, progress, groups, ledger, reports), before)

    def test_inputs_are_not_mutated_on_done(self):
        book = make_book()
        progress = {
            "A-1:normal": {"state": "cleared"},
            "A-1:raid": {"state": "cleared"},
        }
        groups = ["A"]
        ledger = make_ledger()
        before = copy.deepcopy((book, progress, groups, ledger))
        completion.summarize_execution(book, progress, groups, ledger)
        self.assertEqual((book, progress, groups, ledger), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
