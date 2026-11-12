"""Focused hidden-Tk tests for the next desktop UI polish iteration.

Every window is withdrawn by ``AutoStoryDesktop`` itself.  These tests do not
start a worker, do not open a network connection, and never post a command.
"""

from __future__ import annotations

import pathlib
import sys
import tkinter as tk
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import desktop_ui as ui  # noqa: E402


def make_row(
    index: int,
    *,
    state: str = "uncleared",
    mode: str = "normal",
    blockers: list[str] | None = None,
) -> dict:
    return {
        "code": f"T-{index:04d}",
        "name": f"关卡{index}",
        "title": "测试章",
        "group": "T",
        "zone": "测试章",
        "kind": "main",
        "mode": mode,
        "state": state,
        "blockers": ["尚未扫描账号进度"] if blockers is None else blockers,
    }


def make_rows(count: int) -> list[dict]:
    rows = []
    for index in range(count):
        rows.append(
            make_row(
                index,
                state="cleared" if index % 3 == 0 else "uncleared",
                mode="raid" if index % 5 == 0 else "normal",
            )
        )
    return rows


def make_state(logs: str = "line one\n") -> dict:
    return {
        "status": {"state": "idle", "action": "", "chapter": ""},
        "running": False,
        "operators": {
            "count": 316,
            "time": "2026-10-30T21:11:25+08:00",
            "missing": ["历史未识别干员" * 12],
        },
        "profiles": {"CW-EX": {"title": "孤星"}, "DV": {"title": "绿野幻梦"}},
        "settings": {
            "device": "emulator-5554",
            "max_battles_per_run": 3,
            "max_sanity_per_run": 60,
            "formation_index": 4,
        },
        "logs": logs,
    }


class PolishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ui.AutoStoryDesktop("http://127.0.0.1:18741", ROOT)
        self.addCleanup(self.app.close)

    # -- helpers -------------------------------------------------------
    def refresh(self, rows: list[dict], *, logs: str = "line one\n") -> None:
        self.app._apply_dashboard(
            {
                "state": make_state(logs),
                "coverage": {
                    "chapter_count": 1,
                    "stage_variants": len(rows),
                    "battle_codes": len(rows),
                    "rows": rows,
                },
                "errors": [],
            }
        )
        self.app.tk_root.update_idletasks()

    def codes(self) -> list[str]:
        return [
            str(self.app.tree.item(iid, "values")[0]).split()[0]
            for iid in self.app.tree.get_children()
        ]

    def selected_iid(self) -> tuple[str, ...]:
        return tuple(self.app.tree.selection())

    # -- tests ---------------------------------------------------------
    def test_tests_import_the_candidate_copy(self):
        self.assertEqual(
            pathlib.Path(ui.__file__).resolve(), ROOT / "desktop_ui.py"
        )

    def test_pagination_reaches_every_filtered_row_beyond_200(self):
        self.refresh(make_rows(450))
        self.assertEqual(self.app.filter_count_var.get(), "筛选 450 条")
        self.assertIn("第 1 / 3 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 200)
        self.assertEqual(self.codes()[0], "T-0000")
        self.assertEqual(str(self.app.btn_prev_page.cget("state")), "disabled")
        self.assertEqual(str(self.app.btn_next_page.cget("state")), "normal")

        self.app._next_page()
        self.assertIn("第 2 / 3 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 200)
        self.assertEqual(self.codes()[0], "T-0200")
        self.assertEqual(str(self.app.btn_prev_page.cget("state")), "normal")

        self.app._next_page()
        self.assertIn("第 3 / 3 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 50)
        self.assertEqual(self.codes()[0], "T-0400")
        self.assertEqual(self.codes()[-1], "T-0449")
        self.assertEqual(str(self.app.btn_next_page.cget("state")), "disabled")

        # Local paging only; nothing was queued for POST.
        self.assertTrue(self.app._command_worker.commands.empty())
        self.assertFalse(self.app._command_busy)
        self.assertFalse(self.app._stop_busy)

    def test_search_and_reset_filters_return_to_first_page(self):
        self.refresh(make_rows(450))
        self.app._next_page()
        self.app._next_page()
        self.assertIn("第 3 / 3 页", self.app.page_info_var.get())

        # Even a direct combobox change (no virtual event) starts at page one.
        self.app.state_combo.current(1)  # 已确认待通关
        self.app._render_coverage()
        self.assertEqual(self.app._coverage_page, 0)
        self.assertEqual(self.app.filter_count_var.get(), "筛选 300 条")

        self.app._reset_filters()
        self.app.mode_combo.current(2)  # 突袭
        self.app._on_filter_change()
        self.assertEqual(self.app._coverage_page, 0)

        self.app.search_var.set("T-04")
        self.app._apply_search_now()
        # The raid filter is still active, so only every fifth row matches.
        self.assertEqual(self.app.filter_count_var.get(), "筛选 10 条")
        self.assertIn("第 1 / 1 页", self.app.page_info_var.get())
        self.assertEqual(self.codes()[0], "T-0400")

        self.app._reset_filters()
        self.assertEqual(self.app.search_var.get(), "")
        self.assertEqual(self.app.state_combo.get(), "全部状态")
        self.assertEqual(self.app.mode_combo.get(), "全部难度")
        self.assertEqual(self.app.filter_count_var.get(), "筛选 450 条")
        self.assertIn("第 1 / 3 页", self.app.page_info_var.get())
        self.assertEqual(self.app._coverage_page, 0)
        self.assertTrue(self.app._command_worker.commands.empty())

    def test_selected_details_are_full_and_survive_refreshed_identity(self):
        rows = make_rows(3)
        long_reason = "设备连接中断：" + ("模拟器端口被占用，" * 60)
        rows[2]["blockers"] = [long_reason]
        self.refresh(rows)

        self.app.tree.selection_set("2")
        self.app._on_tree_select()
        details = self.app.details_text.get("1.0", "end-1c")
        for fragment in ("T-0002", "关卡2", "测试章", "分组：T", "难度：普通", "状态：未通关"):
            self.assertIn(fragment, details)
        self.assertIn(long_reason, details)
        self.assertEqual(str(self.app.details_text.cget("state")), "disabled")
        self.assertEqual(str(self.app.btn_copy_details.cget("state")), "normal")
        self.assertEqual(self.selected_iid(), ("2",))

        # Unchanged refresh keeps page, selection and details as-is.
        self.refresh(rows)
        self.assertEqual(self.app._coverage_page, 0)
        self.assertEqual(self.selected_iid(), ("2",))
        self.assertIn(long_reason, self.app.details_text.get("1.0", "end-1c"))

        # Changed row with the same (group/code/mode) keeps the selection and
        # repaints the complete new reason (no truncation in the details box).
        new_reason = "编队冲突：" + ("需要重新选择作业，" * 70)
        rows[2]["blockers"] = [new_reason]
        self.refresh(rows)
        self.assertEqual(self.selected_iid(), ("2",))
        updated = self.app.details_text.get("1.0", "end-1c")
        self.assertIn(new_reason, updated)
        self.assertNotIn(long_reason, updated)

        self.app._copy_details()
        self.assertIn("已复制", self.app.message_var.get())
        self.assertTrue(self.app._command_worker.commands.empty())

    def test_empty_first_snapshot_replaces_loading_placeholder(self):
        self.assertIn("正在读取", self.app.filter_count_var.get())
        self.refresh([])
        self.assertEqual(self.app.filter_count_var.get(), "筛选 0 条")
        self.assertIn("第 1 / 1 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.assertIn("没有匹配", self.app.details_text.get("1.0", "end-1c"))

    def test_stale_page_clamps_and_empty_data_is_safe(self):
        self.refresh(make_rows(450))
        self.app._next_page()
        self.app._next_page()
        self.assertEqual(self.app._coverage_page, 2)

        self.refresh(make_rows(250))
        self.assertEqual(self.app._coverage_page, 1)
        self.assertIn("第 2 / 2 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 50)
        self.assertEqual(self.codes()[0], "T-0200")

        self.refresh([])
        self.assertEqual(self.app.filter_count_var.get(), "筛选 0 条")
        self.assertIn("第 1 / 1 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.assertEqual(str(self.app.btn_prev_page.cget("state")), "disabled")
        self.assertEqual(str(self.app.btn_next_page.cget("state")), "disabled")
        self.assertIn("没有匹配", self.app.details_text.get("1.0", "end-1c"))
        self.assertEqual(self.app._coverage_page, 0)

    def test_danger_tag_is_reserved_for_actionable_blockers(self):
        rows = [
            make_row(0, state="unseen", blockers=["尚未扫描账号进度"]),
            make_row(1, state="uncleared", blockers=["尚未扫描账号进度"]),
            make_row(2, state="uncleared", blockers=["设备连接中断"]),
            make_row(3, state="cleared", blockers=[]),
        ]
        self.refresh(rows)
        tags = {
            iid: self.app.tree.item(iid, "tags")[0]
            for iid in self.app.tree.get_children()
        }
        self.assertEqual(tags["0"], "unseen")
        self.assertEqual(tags["1"], "pending")
        self.assertEqual(tags["2"], "blocked")
        self.assertEqual(tags["3"], "cleared")
        self.assertFalse(ui.blocker_is_actionable("尚未扫描账号进度"))
        self.assertTrue(ui.blocker_is_actionable("设备连接中断"))
        self.assertEqual(ui.state_label("uncleared"), "未通关")

    def test_log_follow_toggle_and_jump_to_latest(self):
        self.assertTrue(self.app.follow_var.get())
        self.assertEqual(str(self.app.chk_follow.cget("text")), "跟随最新")
        self.assertEqual(str(self.app.btn_log_latest.cget("text")), "跳到最新")
        self.assertEqual(str(self.app.log_text.cget("state")), "disabled")

        self.app._replace_logs("\n".join(f"line {i}" for i in range(200)), first=True)
        self.app.tk_root.update_idletasks()
        self.assertGreaterEqual(self.app.log_text.yview()[1], 0.98)

        self.app.log_text.yview_moveto(0.2)
        self.app.tk_root.update_idletasks()
        before = self.app.log_text.yview()[0]
        self.app.follow_var.set(False)
        self.app._replace_logs("\n".join(f"new {i}" for i in range(200)), first=False)
        self.app.tk_root.update_idletasks()
        after = self.app.log_text.yview()
        self.assertLess(after[1], 0.9)
        self.assertAlmostEqual(after[0], before, delta=0.12)

        self.app.follow_var.set(True)
        self.app.log_text.yview_moveto(1.0)
        self.app.tk_root.update_idletasks()
        self.app._replace_logs("\n".join(f"newer {i}" for i in range(200)), first=False)
        self.app.tk_root.update_idletasks()
        self.assertGreaterEqual(self.app.log_text.yview()[1], 0.98)

        self.app.log_text.yview_moveto(0.0)
        self.app._scroll_logs_latest()
        self.app.tk_root.update_idletasks()
        self.assertGreaterEqual(self.app.log_text.yview()[1], 0.98)
        self.assertEqual(str(self.app.log_text.cget("state")), "disabled")
        self.assertTrue(self.app._command_worker.commands.empty())

    def test_shortcuts_are_local_and_do_not_post(self):
        root_bindings = self.app.tk_root.bind()
        self.assertTrue(
            any(
                "control" in sequence.lower() and "f" in sequence.lower()
                for sequence in root_bindings
            )
        )
        self.assertTrue(any("f5" in sequence.lower() for sequence in root_bindings))
        entry_bindings = self.app.search_entry.bind()
        self.assertTrue(
            any("escape" in sequence.lower() for sequence in entry_bindings)
        )

        self.app.notebook.select(self.app.logs_tab)
        self.app.search_var.set("候选作业")
        self.assertEqual(self.app._focus_search(), "break")
        self.assertEqual(
            self.app.notebook.tab(self.app.notebook.select(), "text"), "关卡进度"
        )
        self.assertTrue(self.app.search_entry.selection_present())

        self.assertEqual(self.app._clear_search(), "break")
        self.assertEqual(self.app.search_var.get(), "")
        self.assertEqual(self.app._coverage_page, 0)

        with mock.patch.object(self.app, "_request_poll") as poll:
            self.assertEqual(self.app._poll_shortcut(), "break")
            poll.assert_called_once_with(silent=False)
        self.assertTrue(self.app._command_worker.commands.empty())
        self.assertFalse(self.app._command_busy)

    def test_default_and_minimum_geometry_keep_controls_reachable(self):
        self.refresh(make_rows(450))
        self.app.tk_root.geometry(ui.WINDOW_SIZE)
        self.app.tk_root.update_idletasks()

        self.assertLessEqual(self.app.tk_root.winfo_reqwidth(), ui.MIN_SIZE[0])
        self.assertLessEqual(self.app.tk_root.winfo_reqheight(), 760)
        self.assertLessEqual(
            self.app._actions_inner.winfo_reqwidth(), ui.MIN_SIZE[0] - 28
        )
        self.assertEqual(self.app.tk_root.minsize(), ui.MIN_SIZE)

        rowheight = int(
            self.app.tk_root.tk.call(
                "ttk::style", "lookup", "Treeview", "-rowheight"
            )
        )
        rows = int(self.app.tree.cget("height"))
        self.assertGreaterEqual(rows, 12)
        self.assertGreaterEqual(self.app.tree.winfo_reqheight(), rows * rowheight)
        self.assertEqual(rows, 12)

        # Task controls use grid so the window can allocate them at 1050px.
        for button in (
            self.app.btn_run_all,
            self.app.btn_resume,
            self.app.btn_stop,
            self.app.btn_scan,
            self.app.btn_plan,
            self.app.btn_run,
            self.app.btn_ops,
            self.app.btn_refresh,
        ):
            self.assertEqual(button.winfo_manager(), "grid")
        self.assertEqual(self.app.btn_stop.winfo_manager(), "grid")
        self.assertEqual(str(self.app.btn_stop.cget("state")), "normal")
        self.assertLessEqual(int(self.app.details_text.cget("height")), 3)
        self.assertEqual(self.app.budget_label.winfo_manager(), "grid")
        self.assertEqual(self.app.budget_label.grid_info()["row"], 2)
        self.assertEqual(self.app.message_label.grid_info()["row"], 3)

    def test_close_only_sets_the_ui_stop_event(self):
        self.app.close()
        self.assertTrue(self.app._stop_event.is_set())
        self.assertFalse(self.app._command_busy)
        self.assertTrue(self.app._command_worker.commands.empty())


if __name__ == "__main__":
    unittest.main(verbosity=2)
