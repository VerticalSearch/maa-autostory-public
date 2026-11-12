"""Redesign acceptance tests for the candidate AutoStory desktop UI.

Every window is created withdrawn by ``AutoStoryDesktop``.  These tests only
inspect Tk widget state, requested geometry and local helper results; they do
not deiconify a window, send input, open a network connection or post a
command.
"""

from __future__ import annotations

import pathlib
import sys
import tkinter as tk
import unittest
from tkinter import ttk

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import desktop_ui as ui  # noqa: E402


def make_state(*, running: bool = False, missing: list[str] | None = None) -> dict:
    return {
        "status": {
            "state": "running" if running else "idle",
            "action": "run-all" if running else "",
            "chapter": "ALL" if running else "",
        },
        "running": running,
        "operators": {
            "count": 316,
            "time": "2026-10-30T21:11:25+08:00",
            "source": "local",
            "missing": missing or [],
        },
        "profiles": {"CW-EX": {"title": "孤星"}, "DV": {"title": "绿野幻梦"}},
        "settings": {
            "device": "emulator-5554",
            "max_battles_per_run": 3,
            "max_sanity_per_run": 60,
            "formation_index": 4,
        },
        "logs": "line one\nline two\n",
    }


def make_row(index: int, *, state: str = "uncleared", mode: str = "normal",
             blockers: list[str] | None = None) -> dict:
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
    return [
        make_row(
            index,
            state="cleared" if index % 3 == 0 else "uncleared",
            mode="raid" if index % 5 == 0 else "normal",
        )
        for index in range(count)
    ]


def make_coverage(rows: list[dict]) -> dict:
    confirmed = sum(
        1 for row in rows if str(row.get("state")) not in ("unseen", "unknown")
    )
    cleared = sum(1 for row in rows if str(row.get("state")) == "cleared")
    return {
        "chapter_count": 1,
        "stage_variants": len(rows),
        "battle_codes": len(rows),
        "confirmed": confirmed,
        "cleared": cleared,
        "rows": rows,
    }


class RedesignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = ui.AutoStoryDesktop("http://127.0.0.1:18741", ROOT)
        self.addCleanup(self.app.close)

    # -- helpers -------------------------------------------------------
    def refresh(self, rows: list[dict], *, running: bool = False) -> None:
        self.app._apply_dashboard(
            {
                "state": make_state(running=running),
                "coverage": make_coverage(rows),
                "errors": [],
            }
        )
        self.app.tk_root.update_idletasks()

    def label_texts(self, widget) -> list[str]:
        found: list[str] = []
        for child in widget.winfo_children():
            try:
                value = str(child.cget("text"))
            except tk.TclError:
                value = ""
            if value:
                found.append(value)
            found.extend(self.label_texts(child))
        return found

    # -- acceptance ----------------------------------------------------
    def test_design_tokens_window_contract_and_shu_icon(self):
        self.assertEqual(ui.TITLE, "MAA 自动推图助手")
        self.assertEqual(ui.WINDOW_SIZE, "1100x760")
        self.assertEqual(ui.MIN_SIZE, (1050, 720))
        self.assertEqual(ui.MAX_COVERAGE_ROWS, 200)
        self.assertEqual(ui.SIDEBAR, "#173235")
        self.assertEqual(ui.BG, "#F2F5F3")
        self.assertEqual(ui.CARD, "#FFFFFF")
        self.assertEqual(ui.ACCENT, "#237C71")
        self.assertEqual(ui.TEXT, "#213A38")
        self.assertEqual(ui.MUTED, "#5B706D")
        self.assertEqual(ui.SAGE_ACTIVE, "#DCE9E1")
        self.assertEqual(self.app.tk_root.minsize(), ui.MIN_SIZE)
        self.assertEqual(self.app.tk_root.title(), ui.TITLE)

        if (ROOT / "assets" / "shu.ico").is_file():
            self.assertEqual(self.app._icon_path.name, "shu.ico")
        else:
            self.assertEqual(self.app._icon_path.name, "app.ico")
        if (ROOT / "assets" / "shu.png").is_file():
            self.assertIsNotNone(self.app._shu_photo)
            kinds = [
                self.app._sidebar_portrait.type(item)
                for item in self.app._sidebar_portrait.find_all()
            ]
            self.assertIn("image", kinds)

    def test_sidebar_brand_navigation_and_current_task_block(self):
        self.assertEqual(self.app.sidebar.cget("bg"), ui.SIDEBAR)
        self.assertEqual(
            int(self.app.tk_root.columnconfigure(0)["minsize"]), ui.SIDEBAR_WIDTH
        )
        texts = self.label_texts(self.app.sidebar)
        for expected in ("MAA", "自动推图助手", "当前任务", "全目录", "导航"):
            self.assertIn(expected, texts)
        self.assertEqual(self.app.btn_nav_coverage.cget("text"), "关卡进度")
        self.assertEqual(self.app.btn_nav_logs.cget("text"), "运行记录")
        self.assertEqual(int(self.app.btn_nav_coverage.cget("takefocus")), 1)
        self.assertEqual(self.app.btn_nav_coverage.cget("bg"), ui.SAGE_ACTIVE)
        self.assertEqual(self.app.btn_nav_logs.cget("bg"), ui.SIDEBAR)

        self.refresh(make_rows(2), running=True)
        self.assertEqual(self.app.status_var.get(), "运行中")
        self.assertIn("后台运行中", self.app.workflow_var.get())
        self.assertTrue(self.app.workflow_detail_var.get())
        self.assertEqual(
            self.app.status_value_label.cget("fg"), "#7FD1AF"
        )

        # Stop is a persistent action anchored near the sidebar bottom,
        # below the global actions and above the footer.
        stop_frame = self.app.btn_stop.master
        self.assertEqual(stop_frame.grid_info()["row"], 5)
        self.assertEqual(str(self.app.btn_stop.cget("state")), "normal")
        self.assertEqual(self.app.btn_stop.cget("text"), "停止当前任务")

    def test_hidden_tabs_switch_existing_notebook_and_update_nav(self):
        style = ttk.Style(self.app.tk_root)
        layout = style.layout("TNotebook.Tab")
        self.assertTrue(not layout or layout[0][0] == "null")
        self.assertEqual(
            [self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()],
            ["关卡进度", "运行记录"],
        )

        self.app._show_logs()
        self.app.tk_root.update_idletasks()
        self.assertEqual(self.app.notebook.select(), str(self.app.logs_tab))
        self.assertEqual(self.app.page_title_var.get(), "运行记录")
        self.assertEqual(self.app.btn_nav_logs.cget("bg"), ui.SAGE_ACTIVE)
        self.assertEqual(self.app.btn_nav_coverage.cget("bg"), ui.SIDEBAR)
        self.assertEqual(self.app.coverage_header.winfo_manager(), "")

        self.app.search_var.set("候选作业")
        self.assertEqual(self.app._focus_search(), "break")
        self.app.tk_root.update_idletasks()
        self.assertEqual(
            self.app.notebook.tab(self.app.notebook.select(), "text"), "关卡进度"
        )
        self.assertEqual(self.app.page_title_var.get(), "关卡进度")
        self.assertEqual(self.app.btn_nav_coverage.cget("bg"), ui.SAGE_ACTIVE)
        self.assertTrue(self.app.search_entry.selection_present())
        self.assertTrue(self.app._command_worker.commands.empty())

    def test_main_list_dominates_with_tall_neutral_alternating_rows(self):
        self.refresh(make_rows(6))
        rowheight = int(
            self.app.tk_root.tk.call(
                "ttk::style", "lookup", "Treeview", "-rowheight"
            )
        )
        rows = int(self.app.tree.cget("height"))
        self.assertGreaterEqual(rowheight, 24)
        self.assertLessEqual(rowheight, 28)
        self.assertGreaterEqual(rows, 9)
        self.assertGreaterEqual(self.app.tree.winfo_reqheight(), rows * rowheight)
        self.assertLessEqual(rows, ui.TREE_VISIBLE_ROWS)
        # The list, not the telemetry or detail strip, owns the vertical space.
        self.assertGreater(
            self.app.tree.winfo_reqheight(),
            3 * self.app.details_panel.winfo_reqheight(),
        )

        items = self.app.tree.get_children()
        self.assertTrue(items)
        semantic = {"cleared", "pending", "blocked", "unseen"}
        for index, iid in enumerate(items):
            tags = self.app.tree.item(iid, "tags")
            self.assertIn(tags[0], semantic)
            expected_zebra = "odd" if index % 2 else "even"
            self.assertIn(expected_zebra, tags)
        odd_bg = self.app.tree.tag_configure("odd")["background"]
        even_bg = self.app.tree.tag_configure("even")["background"]
        self.assertEqual(str(odd_bg).upper(), ui.ROW_ALT)
        self.assertEqual(str(even_bg).upper(), ui.CARD)
        cleared = self.app.tree.tag_configure("cleared")
        self.assertEqual(str(cleared["foreground"]).upper(), ui.TEXT)
        self.assertFalse(str(cleared["background"]).strip())
        blocked = self.app.tree.tag_configure("blocked")
        self.assertEqual(str(blocked["foreground"]).upper(), ui.DANGER)

    def test_telemetry_is_compact_and_progress_meter_uses_real_values(self):
        rows = [
            make_row(0, state="cleared"),
            make_row(1, state="unseen"),
            make_row(2, state="unseen"),
        ]
        self.refresh(rows)
        self.assertEqual(self.app.progress_var.get(), "1 / 3")
        self.assertIn("已通关 1", self.app.progress_detail_var.get())
        self.assertEqual(self.app.operators_var.get(), "316 名")
        self.assertIn("快照 2026-10-30 21:11:25", self.app.operators_detail_var.get())
        self.assertAlmostEqual(self.app._progress_ratio, 1 / 3, places=2)
        items = self.app.progress_canvas.find_all()
        self.assertEqual(len(items), 2)
        coords = self.app.progress_canvas.coords(items[1])
        self.assertGreater(coords[2], 0)
        self.assertLess(coords[2], int(self.app.progress_canvas.cget("width")))
        # Account progress and operator snapshot share one band but do not use
        # identical same-sized card frames.
        self.assertEqual(self.app.operators_var.get().split()[0], "316")

    def test_chapter_actions_and_stop_fit_the_minimum_window(self):
        self.refresh(make_rows(6))
        self.app.tk_root.update_idletasks()
        self.assertLessEqual(self.app.tk_root.winfo_reqwidth(), ui.MIN_SIZE[0])
        self.assertLessEqual(self.app.tk_root.winfo_reqheight(), 760)
        self.assertLessEqual(self.app.sidebar.winfo_reqheight(), ui.MIN_SIZE[1])
        available = ui.MIN_SIZE[0] - ui.SIDEBAR_WIDTH - 40
        self.assertLessEqual(self.app.chapter_row.winfo_reqwidth(), available)

        labels = {
            self.app.btn_run_all: "全目录推图",
            self.app.btn_resume: "继续上轮全目录",
            self.app.btn_stop: "停止当前任务",
            self.app.btn_scan: "扫描本分区",
            self.app.btn_plan: "筛选作业",
            self.app.btn_run: "本分区推图",
            self.app.btn_ops: "更新干员",
            self.app.btn_refresh: "刷新状态",
            self.app.btn_web: "网页控制页",
        }
        for button, text in labels.items():
            self.assertEqual(button.cget("text"), text)
            self.assertEqual(button.winfo_manager(), "grid")
        self.assertEqual(str(self.app.btn_stop.cget("state")), "normal")
        self.assertLessEqual(
            self.app._actions_inner.winfo_reqwidth(), ui.SIDEBAR_WIDTH
        )

    def test_selected_stage_details_panel_is_distinct_and_scrollable(self):
        self.assertEqual(self.app.details_panel.cget("bg"), ui.SOFT_SURFACE)
        self.assertEqual(int(self.app.details_panel.cget("highlightthickness")), 1)
        height = int(self.app.details_text.cget("height"))
        self.assertGreaterEqual(height, 2)
        self.assertLessEqual(height, 3)
        self.assertEqual(str(self.app.details_text.cget("state")), "disabled")
        long_reason = "设备连接中断：" + ("模拟器端口被占用，" * 40)
        rows = make_rows(2)
        rows[1]["blockers"] = [long_reason]
        self.refresh(rows)

        self.app.tree.selection_set("1")
        self.app._on_tree_select()
        self.assertEqual(self.app.tree.item("1", "tags")[0], "selected")
        self.assertEqual(
            str(self.app.tree.tag_configure("selected")["background"]).upper(),
            ui.SELECTION_BG,
        )
        details = self.app.details_text.get("1.0", "end-1c")
        for fragment in ("T-0001", "关卡1", "测试章", "难度：普通", "状态：未通关"):
            self.assertIn(fragment, details)
        self.assertIn(long_reason, details)
        self.assertEqual(str(self.app.btn_copy_details.cget("state")), "normal")
        self.assertEqual(self.app.btn_copy_details.cget("text"), "复制详情")

        self.app.search_var.set("no_matching_stage")
        self.app._render_coverage()
        self.app.tk_root.update_idletasks()
        self.assertIn("没有匹配", self.app.details_text.get("1.0", "end-1c"))
        self.assertEqual(str(self.app.btn_copy_details.cget("state")), "disabled")
        self.assertTrue(self.app._command_worker.commands.empty())

    def test_paging_and_filters_keep_the_200_row_contract(self):
        self.refresh(make_rows(201))
        self.assertEqual(self.app.filter_count_var.get(), "筛选 201 条")
        self.assertIn("第 1 / 2 页", self.app.page_info_var.get())
        self.assertEqual(len(self.app.tree.get_children()), 200)
        self.assertEqual(str(self.app.btn_prev_page.cget("state")), "disabled")
        self.assertEqual(str(self.app.btn_next_page.cget("state")), "normal")

        self.app._next_page()
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.assertIn("第 2 / 2 页", self.app.page_info_var.get())
        self.assertEqual(
            str(self.app.tree.item(self.app.tree.get_children()[0], "values")[0]).split()[0],
            "T-0200",
        )
        self.app._prev_page()
        self.assertEqual(self.app._coverage_page, 0)
        self.app.search_var.set("T-010")
        self.app._apply_search_now()
        self.assertEqual(self.app._coverage_page, 0)
        self.assertEqual(self.app.filter_count_var.get(), "筛选 10 条")
        self.assertLessEqual(
            int(self.app.tree.cget("height")), ui.MAX_COVERAGE_ROWS
        )
        self.assertTrue(self.app._command_worker.commands.empty())

    def test_log_page_keeps_shell_follow_and_jump_latest(self):
        self.app._show_logs()
        self.app.tk_root.update_idletasks()
        self.assertEqual(self.app.page_title_var.get(), "运行记录")
        self.assertEqual(self.app.coverage_header.winfo_manager(), "")
        self.assertEqual(self.app.btn_refresh.winfo_manager(), "grid")
        self.assertEqual(self.app.btn_web.winfo_manager(), "grid")
        self.assertTrue(self.app.follow_var.get())
        self.assertEqual(self.app.chk_follow.cget("text"), "跟随最新")
        self.assertEqual(self.app.btn_log_latest.cget("text"), "跳到最新")
        self.assertEqual(str(self.app.log_text.cget("state")), "disabled")
        self.app._replace_logs("\n".join(f"line {i}" for i in range(200)), first=True)
        self.app.tk_root.update_idletasks()
        self.assertGreaterEqual(self.app.log_text.yview()[1], 0.98)
        self.app.log_text.yview_moveto(0.0)
        self.app._scroll_logs_latest()
        self.app.tk_root.update_idletasks()
        self.assertGreaterEqual(self.app.log_text.yview()[1], 0.98)

    def test_missing_assets_fall_back_to_geometric_brand_mark(self):
        missing_root = ROOT / "_missing_assets_root_"
        fallback = ui.AutoStoryDesktop("http://127.0.0.1:18741", missing_root)
        try:
            fallback.tk_root.update_idletasks()
            self.assertIsNone(fallback._icon_path)
            self.assertIsNone(fallback._shu_photo)
            kinds = [
                fallback._sidebar_portrait.type(item)
                for item in fallback._sidebar_portrait.find_all()
            ]
            self.assertIn("oval", kinds)
            self.assertIn("text", kinds)
        finally:
            fallback.close()

    def test_flat_styles_focus_and_shortcuts_are_local(self):
        style = ttk.Style(self.app.tk_root)
        for name in ("TButton", "Accent.TButton", "Danger.TButton", "Quiet.TButton"):
            self.assertEqual(str(style.lookup(name, "relief")), "flat")
            self.assertEqual(int(style.lookup(name, "borderwidth")), 0)
        accent_states = dict(style.map("Accent.TButton", "background"))
        self.assertIn("active", accent_states)
        self.assertIn("disabled", accent_states)
        danger_states = dict(style.map("Danger.TButton", "background"))
        self.assertEqual(danger_states["active"], ui.DANGER_HOVER)
        self.assertEqual(dict(style.map("TEntry", "bordercolor"))["focus"], ui.ACCENT)

        root_bindings = self.app.tk_root.bind()
        self.assertTrue(
            any(
                "control" in sequence.lower() and "f" in sequence.lower()
                for sequence in root_bindings
            )
        )
        self.assertTrue(any("f5" in sequence.lower() for sequence in root_bindings))
        self.assertEqual(int(self.app.btn_nav_coverage.cget("takefocus")), 1)
        self.assertEqual(int(self.app.btn_nav_logs.cget("takefocus")), 1)
        self.assertTrue(self.app._command_worker.commands.empty())
        self.assertFalse(self.app._command_busy)
        self.assertFalse(self.app._stop_busy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
