"""Isolated tests for candidate/desktop_ui.py.

These tests use a local mock HTTP server and Tk's withdrawn smoke path only.
No production file, ADB device, game state, or visible desktop window is used.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import tkinter as tk
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
CANDIDATE = PROJECT_ROOT
sys.path.insert(0, str(CANDIDATE))

import desktop_ui as ui  # noqa: E402


def _state_payload() -> dict:
    return {
        "status": {"state": "idle", "action": "", "chapter": ""},
        "running": False,
        "operators": {
            "count": 316,
            "time": "2026-10-30T21:11:25+08:00",
            "source": "local",
            "missing": ["德·托莱多"],
        },
        "chapters": {"CW": {"title": "孤星"}},
        "profiles": {
            "CW-EX": {"title": "孤星", "kind": "side"},
            "DV": {"title": "绿野幻梦", "kind": "side"},
        },
        "progress": [{"code": "DV-1", "mode": "normal", "state": "cleared"}],
        "plan": {"stages": []},
        "results": [],
        "settings": {
            "device": "emulator-5554",
            "max_battles_per_run": 3,
            "max_sanity_per_run": 60,
            "formation_index": 4,
            "port": 18741,
        },
        "logs": "line one\nline two\n",
    }


def _coverage_payload() -> dict:
    return {
        "at": "2026-10-30T21:12:00+08:00",
        "chapter_count": 2,
        "stage_variants": 3,
        "battle_codes": 2,
        "confirmed": 1,
        "cleared": 1,
        "rows": [
            {
                "code": "CW-EX-8",
                "name": "关卡八",
                "title": "孤星",
                "group": "CW-EX",
                "zone": "孤星",
                "kind": "side",
                "mode": "normal",
                "state": "cleared",
                "blockers": [],
            },
            {
                "code": "CW-EX-8",
                "name": "关卡八",
                "title": "孤星",
                "group": "CW-EX",
                "zone": "孤星",
                "kind": "side",
                "mode": "raid",
                "state": "uncleared",
                "blockers": ["尚未扫描账号进度", "候选作业均不符合当前干员条件"],
            },
            {
                "code": "DV-1",
                "name": "坍塌",
                "title": "绿野幻梦",
                "group": "DV",
                "zone": "绿野幻梦",
                "kind": "side",
                "mode": "normal",
                "state": "unseen",
                "blockers": ["尚未扫描账号进度"],
            },
        ],
    }


class MockAutoStoryServer:
    """Small loopback server exposing the real AutoStory response shapes."""

    def __init__(self) -> None:
        self.token = "old-token-1234567890"
        self.state = _state_payload()
        self.coverage = _coverage_payload()
        self.state_error = False
        self.coverage_error = False
        self.force_403_once = False
        self.always_403 = False
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, name="mock-autostory", daemon=True
        )

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj: dict) -> None:
                self._send(
                    status,
                    json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )

            def do_GET(self):
                with outer.lock:
                    outer.gets.append(self.path)
                if self.path == "/":
                    html = (
                        "<!doctype html><script>const token='%s';</script>"
                        % outer.token
                    )
                    self._send(
                        200, html.encode("utf-8"), "text/html; charset=utf-8"
                    )
                    return
                if self.path == "/api/state":
                    if outer.state_error:
                        self._json(500, {"error": "mock state failure"})
                    else:
                        self._json(200, outer.state)
                    return
                if self.path == "/api/coverage":
                    if outer.coverage_error:
                        self._json(500, {"error": "mock coverage failure"})
                    else:
                        self._json(200, outer.coverage)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except (TypeError, ValueError):
                    length = 0
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    body = {}
                token = self.headers.get("X-AutoStory-Token")
                with outer.lock:
                    outer.posts.append(
                        {
                            "path": self.path,
                            "token": token,
                            "body": body,
                        }
                    )
                    reject = outer.always_403
                    if outer.force_403_once:
                        reject = True
                        outer.force_403_once = False
                        outer.token = "new-token-0987654321"
                if reject or token != outer.token:
                    self._json(403, {"error": "请求校验失败，请刷新本地页面"})
                    return
                if self.path not in ("/api/action", "/api/stop"):
                    self._json(404, {"error": "not found"})
                    return
                message = "停止信号已发送" if self.path == "/api/stop" else "后台任务已启动"
                self._json(200, {"message": message, "pid": 1234})

        return Handler

    def start(self) -> "MockAutoStoryServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


class PureHelperTests(unittest.TestCase):
    def test_window_contract_constants(self):
        self.assertEqual(ui.TITLE, "MAA 自动推图助手")
        self.assertEqual(ui.WINDOW_SIZE, "1100x760")
        self.assertEqual(ui.MAX_COVERAGE_ROWS, 200)
        self.assertEqual(ui.POLL_SECONDS, 4.0)

    def test_normalize_base_url_accepts_loopback_and_trailing_slash(self):
        self.assertEqual(
            ui.normalize_base_url(" http://127.0.0.1:18741/ "),
            "http://127.0.0.1:18741",
        )
        self.assertEqual(
            ui.normalize_base_url("http://localhost:18741"),
            "http://localhost:18741",
        )

    def test_normalize_base_url_rejects_non_loopback_and_paths(self):
        for value in (
            "http://192.0.2.10:18741",
            "https://example.com",
            "http://127.0.0.1:18741/admin",
            "http://127.0.0.1:18741/?x=1",
            "ftp://127.0.0.1:18741",
            "",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ui.normalize_base_url(value)

    def test_parse_token_single_and_double_quotes(self):
        self.assertEqual(
            ui.parse_token("<script>const token='abcDEF1234567890';</script>"),
            "abcDEF1234567890",
        )
        self.assertEqual(
            ui.parse_token('<script>const token = "abcDEF1234567890";</script>'),
            "abcDEF1234567890",
        )
        self.assertEqual(ui.parse_token("<script>const token='abc';</script>"), "abc")
        self.assertIsNone(ui.parse_token("<script>const token='';</script>"))
        self.assertIsNone(ui.parse_token("no token"))

    def test_filter_coverage_by_query_state_mode_and_cap(self):
        rows = _coverage_payload()["rows"]
        visible, total = ui.filter_coverage(rows, "候选作业", "blocked", "raid")
        self.assertEqual(total, 1)
        self.assertEqual(visible[0]["code"], "CW-EX-8")

        pending, total = ui.filter_coverage(rows, "", "pending", "all")
        self.assertEqual(total, 1)
        self.assertEqual(pending[0]["state"], "uncleared")

        unseen, total = ui.filter_coverage(rows, "坍塌", "unseen", "normal")
        self.assertEqual(total, 1)
        self.assertEqual(unseen[0]["code"], "DV-1")

        many = [{"code": f"X-{i}", "state": "unseen", "mode": "normal", "blockers": []}
                for i in range(250)]
        only, total = ui.filter_coverage(many, "", "all", "all", limit=45)
        self.assertEqual(total, 250)
        self.assertEqual(len(only), 45)

    def test_coverage_summary_lists_counts_and_modes(self):
        summary = ui.coverage_summary(_coverage_payload())
        self.assertIn("3 条难度记录", summary)
        self.assertIn("普通 2", summary)
        self.assertIn("突袭 1", summary)

    def test_status_detail_surfaces_paused_reason(self):
        detail = ui.status_detail(
            {
                "state": "error",
                "action": "run-all",
                "chapter": "ALL",
                "message": "设备连接中断",
            }
        )
        self.assertIn("全目录推图", detail)
        self.assertIn("设备连接中断", detail)
        self.assertEqual(ui.status_label("error"), "已暂停")

    def test_budget_uses_api_settings(self):
        budget = ui.format_budget(_state_payload()["settings"])
        self.assertIn("3 次正式作战", budget)
        self.assertIn("60 理智", budget)
        self.assertIn("第 4 编队", budget)
        self.assertIn("emulator-5554", budget)

    def test_state_and_mode_labels_are_chinese(self):
        self.assertEqual(ui.state_label("cleared"), "已完成目标难度")
        self.assertEqual(ui.mode_label("raid"), "突袭")
        self.assertEqual(ui.mode_label("tough"), "磨难 / 险地")


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server = MockAutoStoryServer().start()
        self.addCleanup(self.server.stop)

    def test_403_refreshes_token_and_retries_once(self):
        self.server.force_403_once = True
        client = ui.ApiClient(self.server.url, timeout=3)
        result = client.post_action("run", "CW-EX")
        self.assertIn("message", result)
        self.assertEqual(len(self.server.posts), 2)
        self.assertNotEqual(
            self.server.posts[0]["token"], self.server.posts[1]["token"]
        )
        self.assertEqual(self.server.gets.count("/"), 2)
        self.assertEqual(self.server.posts[1]["body"]["action"], "run")
        self.assertEqual(self.server.posts[1]["body"]["chapter"], "CW-EX")

    def test_403_retry_is_bounded_to_one_retry(self):
        self.server.always_403 = True
        client = ui.ApiClient(self.server.url, timeout=3)
        with self.assertRaises(ui.ApiError) as caught:
            client.post_action("run", "CW-EX")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(len(self.server.posts), 2)
        self.assertEqual(self.server.gets.count("/"), 2)

    def test_stop_uses_stop_endpoint_and_token_header(self):
        client = ui.ApiClient(self.server.url, timeout=3)
        client.post_action("stop", "CW-EX")
        self.assertEqual(self.server.posts[-1]["path"], "/api/stop")
        self.assertEqual(self.server.posts[-1]["token"], self.server.token)
        self.assertEqual(self.server.posts[-1]["body"]["action"], "stop")

    def test_dashboard_payload_shape(self):
        client = ui.ApiClient(self.server.url, timeout=3)
        state, coverage, errors = client.fetch_dashboard()
        self.assertEqual(errors, [])
        self.assertEqual(state["operators"]["count"], 316)
        self.assertEqual(len(coverage["rows"]), 3)


class WidgetStateTests(unittest.TestCase):
    def test_dashboard_state_updates_tiles_filters_and_button_gates(self):
        app = ui.AutoStoryDesktop("http://127.0.0.1:18741", PROJECT_ROOT)
        try:
            state = _state_payload()
            state["running"] = True
            state["status"] = {
                "state": "running",
                "action": "run-all",
                "chapter": "ALL",
            }
            coverage = _coverage_payload()
            app._apply_dashboard(
                {"state": state, "coverage": coverage, "errors": []}
            )

            self.assertEqual(app.status_var.get(), "运行中")
            self.assertIn("全目录推图", app.status_detail_var.get())
            self.assertEqual(app.operators_var.get(), "316 名")
            self.assertIn("2026-10-30 21:11:25", app.operators_detail_var.get())
            self.assertEqual(app.progress_var.get(), "1 / 3")
            self.assertIn("已通关 1", app.progress_detail_var.get())
            self.assertEqual(app.chapter_var.get(), "CW-EX · 孤星")
            self.assertIn("普通 2", app.coverage_summary_var.get())
            self.assertEqual(app.filter_count_var.get(), "筛选 3 条")
            self.assertEqual(len(app.tree.get_children()), 3)
            self.assertIn("line one", app.log_text.get("1.0", "end-1c"))

            # Running disables every mutating action, but stop/refresh/web stay usable.
            self.assertEqual(str(app.btn_run.cget("state")), "disabled")
            self.assertEqual(str(app.btn_run_all.cget("state")), "disabled")
            self.assertEqual(str(app.btn_stop.cget("state")), "normal")
            self.assertEqual(str(app.btn_refresh.cget("state")), "normal")
            self.assertEqual(str(app.btn_web.cget("state")), "normal")

            # Search/filter and mode filter both re-render the capped table.
            app.search_var.set("候选作业")
            app._render_coverage()
            self.assertEqual(len(app.tree.get_children()), 1)
            self.assertIn("筛选 1 条", app.filter_count_var.get())

            app.search_var.set("")
            app.mode_combo.current(2)  # 突袭
            app._render_coverage()
            self.assertEqual(len(app.tree.get_children()), 1)

            # Recovery clears the persistent connection-error text.
            app._apply_dashboard(
                {"state": state, "coverage": coverage, "errors": ["状态接口：超时"]}
            )
            self.assertIn("后台读取失败", app.message_var.get())
            idle = _state_payload()
            app._apply_dashboard(
                {"state": idle, "coverage": coverage, "errors": []}
            )
            self.assertEqual(app.message_var.get(), "后台连接已恢复。")
            self.assertEqual(str(app.btn_run.cget("state")), "normal")
            self.assertEqual(str(app.btn_stop.cget("state")), "normal")
        finally:
            app.close()


class NormalLoopTests(unittest.TestCase):
    def test_normal_loop_polls_once_before_four_seconds_and_does_not_post(self):
        server = MockAutoStoryServer().start()
        self.addCleanup(server.stop)
        observed = {}

        with mock.patch.object(tk.Tk, "deiconify", lambda self: None):
            app = ui.AutoStoryDesktop(server.url, PROJECT_ROOT)

            def finish():
                observed["message"] = app.message_var.get()
                observed["connection"] = app.connection_var.get()
                app.close()

            try:
                app.tk_root.after(1800, finish)
                app.run()
            finally:
                app.close()
        self.assertGreaterEqual(server.gets.count("/api/state"), 1)
        self.assertGreaterEqual(server.gets.count("/api/coverage"), 1)
        self.assertEqual(server.gets.count("/api/state"), 1)
        self.assertEqual(server.gets.count("/api/coverage"), 1)
        self.assertEqual(server.posts, [])
        self.assertEqual(
            observed["message"],
            "已连接本机后台。请选择章节后手动开始；关闭窗口不会停止后台任务。",
        )
        self.assertIn("已连接", observed["connection"])


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.server = MockAutoStoryServer().start()
        self.addCleanup(self.server.stop)

    def test_smoke_is_withdrawn_fetches_once_and_does_not_post(self):
        with mock.patch.object(
            tk.Tk, "deiconify", side_effect=AssertionError("smoke must stay withdrawn")
        ):
            result = ui.run_ui(self.server.url, PROJECT_ROOT, smoke=True)
        self.assertEqual(
            set(result), {"ok", "profiles", "coverage_rows", "tk"}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["profiles"], 2)
        self.assertEqual(result["coverage_rows"], 3)
        self.assertEqual(result["tk"], tk.TkVersion)
        self.assertEqual(self.server.posts, [])
        self.assertIn("/api/state", self.server.gets)
        self.assertIn("/api/coverage", self.server.gets)

    def test_smoke_raises_when_initial_view_fetch_fails(self):
        self.server.state_error = True
        self.server.coverage_error = True
        with self.assertRaises(RuntimeError):
            ui.run_ui(self.server.url, PROJECT_ROOT, smoke=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
