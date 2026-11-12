"""Focused candidate regression tests for the AutoStory desktop flow.

Hidden Tk only: every window is withdrawn and no deiconify/mouse/desktop API is
used.  The HTTP server is loopback-only and never launches a worker or game.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import tkinter as tk
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import desktop_ui as ui  # noqa: E402


def state_payload(running: bool = False) -> dict:
    return {
        "status": {"state": "running" if running else "idle", "action": "run-all" if running else "", "chapter": "ALL" if running else ""},
        "running": running,
        "operators": {"count": 316, "time": "2026-10-30T21:11:25+08:00", "source": "local", "missing": []},
        "profiles": {"CW-EX": {"title": "孤星"}, "DV": {"title": "绿野幻梦"}},
        "settings": {"device": "emulator-5554", "max_battles_per_run": 3, "max_sanity_per_run": 60, "formation_index": 4},
        "logs": "line one\n",
    }


def coverage_payload() -> dict:
    return {
        "at": "2026-10-30T21:12:00+08:00",
        "chapter_count": 1,
        "stage_variants": 2,
        "battle_codes": 2,
        "confirmed": 1,
        "cleared": 0,
        "rows": [
            {"code": "CW-EX-8", "name": "关卡八", "title": "孤星", "group": "CW-EX", "mode": "normal", "state": "cleared", "blockers": []},
            {"code": "CW-EX-9", "name": "关卡九", "title": "孤星", "group": "CW-EX", "mode": "raid", "state": "uncleared", "blockers": ["尚未扫描账号进度"]},
        ],
    }


class MockServer:
    """Loopback server with both the new dashboard and legacy endpoints."""

    def __init__(self, *, dashboard: bool = True, block_dashboard: float = 0.0, dashboard_null_on_revision: bool = False) -> None:
        self.dashboard_supported = dashboard
        self.block_dashboard = block_dashboard
        self.dashboard_null_on_revision = dashboard_null_on_revision
        self.token = "candidate-token-123456"
        self.state = state_payload()
        self.coverage = coverage_payload()
        self.dashboard_paths: list[str] = []
        self.state_paths: list[str] = []
        self.coverage_paths: list[str] = []
        self.posts: list[dict] = []
        self.dashboard_seen = threading.Event()
        self.stop_seen = threading.Event()
        self.lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, obj: dict) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/":
                    body = f"<script>const token='{outer.token}';</script>".encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.startswith("/api/dashboard"):
                    with outer.lock:
                        outer.dashboard_paths.append(self.path)
                    outer.dashboard_seen.set()
                    if not outer.dashboard_supported:
                        self._send(404, {"error": "not found"})
                        return
                    if outer.block_dashboard:
                        time.sleep(outer.block_dashboard)
                    with outer.lock:
                        count = len(outer.dashboard_paths)
                    unchanged = outer.dashboard_null_on_revision and "revision=" in self.path
                    self._send(
                        200,
                        {
                            "state": outer.state,
                            "coverage": None if unchanged else outer.coverage,
                            "revision": f"rev-{count}",
                        },
                    )
                    return
                if self.path == "/api/state":
                    with outer.lock:
                        outer.state_paths.append(self.path)
                    self._send(200, outer.state)
                    return
                if self.path == "/api/coverage":
                    with outer.lock:
                        outer.coverage_paths.append(self.path)
                    self._send(200, outer.coverage)
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    body = {}
                with outer.lock:
                    outer.posts.append({"path": self.path, "body": body, "token": self.headers.get("X-AutoStory-Token")})
                if self.path == "/api/stop":
                    outer.stop_seen.set()
                    self._send(200, {"message": "停止信号已发送"})
                    return
                self._send(200, {"message": "后台任务已启动", "pid": 123})

        return Handler

    def start(self) -> "MockServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


class LayoutTests(unittest.TestCase):
    def test_populated_snapshot_details_do_not_force_window_wider(self):
        app=ui.AutoStoryDesktop('http://127.0.0.1:18741',ROOT)
        try:
            state=state_payload()
            state['operators']['missing']=['历史未识别干员']
            app._apply_dashboard({'state':state,'coverage':coverage_payload(),'errors':[]})
            app.tk_root.update_idletasks()
            self.assertLessEqual(app.tk_root.winfo_reqwidth(),ui.MIN_SIZE[0])
            self.assertLessEqual(app.tk_root.winfo_reqheight(),760)
        finally:app.close()

    def test_withdrawn_default_layout_requests_twelve_plus_rows(self):
        app = ui.AutoStoryDesktop("http://127.0.0.1:18741", ROOT)
        try:
            app.tk_root.geometry(ui.WINDOW_SIZE)
            app.tk_root.update_idletasks()
            rowheight = int(app.tk_root.tk.call("ttk::style", "lookup", "Treeview", "-rowheight"))
            rows = int(app.tree.cget("height"))
            self.assertGreaterEqual(rows, 12)
            self.assertGreaterEqual(app.tree.winfo_reqheight(), 12 * rowheight)
            # The requested fixed stack plus the tree must fit the default
            # client area; otherwise Tk would shrink the tree below 12 rows.
            self.assertLessEqual(app.tk_root.winfo_reqheight(), 760)
            self.assertEqual(app.tk_root.minsize(), ui.MIN_SIZE)
            tabs = [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()]
            self.assertEqual(tabs, ["关卡进度", "运行记录"])
            self.assertEqual(str(app.btn_stop.cget("state")), "normal")
        finally:
            app.close()


class RevisionAndFallbackTests(unittest.TestCase):
    def test_revision_null_reuses_last_coverage(self):
        server = MockServer(dashboard_null_on_revision=True).start()
        self.addCleanup(server.stop)
        client = ui.ApiClient(server.url, timeout=3)
        first_state, first_coverage, first_errors = client.fetch_dashboard()
        second_state, second_coverage, second_errors = client.fetch_dashboard()
        self.assertEqual(first_errors, [])
        self.assertEqual(second_errors, [])
        self.assertIsNotNone(first_state)
        self.assertIsNotNone(second_state)
        self.assertEqual(first_coverage["rows"], second_coverage["rows"])
        self.assertEqual(server.dashboard_paths[0], "/api/dashboard")
        self.assertIn("revision=rev-1", server.dashboard_paths[1])
        self.assertEqual(server.state_paths, [])
        self.assertEqual(server.coverage_paths, [])

    def test_smoke_uses_new_dashboard_without_posting(self):
        server = MockServer().start()
        self.addCleanup(server.stop)
        with mock.patch.object(
            tk.Tk, "deiconify", side_effect=AssertionError("smoke must stay withdrawn")
        ):
            result = ui.run_ui(server.url, ROOT, smoke=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage_rows"], 2)
        self.assertEqual(server.posts, [])
        self.assertTrue(server.dashboard_paths)

    def test_404_dashboard_falls_back_and_caches_old_transport(self):
        server = MockServer(dashboard=False).start()
        self.addCleanup(server.stop)
        client = ui.ApiClient(server.url, timeout=3)
        state, coverage, errors = client.fetch_dashboard()
        self.assertEqual(errors, [])
        self.assertEqual(state["operators"]["count"], 316)
        self.assertEqual(len(coverage["rows"]), 2)
        self.assertEqual(server.dashboard_paths, ["/api/dashboard"])
        state, coverage, errors = client.fetch_dashboard()
        self.assertEqual(errors, [])
        self.assertEqual(server.dashboard_paths, ["/api/dashboard"])
        self.assertEqual(len(server.state_paths), 2)
        self.assertEqual(len(server.coverage_paths), 2)


class StatusTests(unittest.TestCase):
    def test_workflow_campaign_and_snapshot_age_are_visible(self):
        app = ui.AutoStoryDesktop("http://127.0.0.1:18741", ROOT)
        try:
            state = state_payload(running=True)
            state["workflow"] = {
                "phase": "formal",
                "chapter": "8",
                "code": "R8-1",
                "mode": "raid",
                "message": "正在执行第 8 章正式作战",
                "updated": "2026-10-30T21:30:00+08:00",
                "completed": 2,
                "total": 5,
            }
            state["campaign"] = {"active_group": "R8", "state": "running", "budget": {"sanity": 12, "limit": 3, "count": 2}}
            app._apply_dashboard({"state": state, "coverage": coverage_payload(), "errors": []})
            self.assertIn("正式作战", app.workflow_var.get())
            self.assertIn("R8-1", app.workflow_var.get())
            self.assertIn("2/5", app.workflow_var.get())
            self.assertIn("第 8 章", app.workflow_var.get())
            self.assertIn("正在执行第 8 章正式作战", app.workflow_detail_var.get())
            self.assertIn("剩余预算 12 理智", app.campaign_var.get())
            self.assertIn("正式作战 2/3 次", app.campaign_var.get())
            self.assertIn("快照 2026-10-30 21:11:25", app.operators_detail_var.get())
            age = ui.format_age("2026-10-30T21:11:25+08:00")
            self.assertTrue(age)
            self.assertIn(age, app.operators_detail_var.get())
        finally:
            app.close()

    def test_unknown_total_does_not_fake_progress_and_stale_poll_is_ignored(self):
        app = ui.AutoStoryDesktop("http://127.0.0.1:18741", ROOT)
        try:
            state = state_payload(running=True)
            state["workflow"] = {"phase": "formal", "completed": 2, "message": "进行中"}
            app._apply_dashboard({"state": state, "coverage": coverage_payload(), "errors": []})
            self.assertNotIn("2/", app.workflow_var.get())
            self.assertEqual(app.status_var.get(), "运行中")

            app._dashboard_epoch = 4
            idle = state_payload(running=False)
            app._apply_dashboard({"state": idle, "coverage": coverage_payload(), "errors": [], "epoch": 3})
            self.assertEqual(app.status_var.get(), "运行中")
        finally:
            app.close()


class StopTransportTests(unittest.TestCase):
    def test_stop_clears_a_cancelled_start_without_leaving_buttons_disabled(self):
        app=ui.AutoStoryDesktop('http://127.0.0.1:18741',ROOT)
        try:
            app._post_command('run','DV','本分区推图')
            self.assertTrue(app._command_busy)
            app._post_stop()
            self.assertTrue(app._stop_busy)
            self.assertEqual(str(app.btn_run.cget('state')),'disabled')
            app._handle_command({'action':'stop','ok':True,'epoch':app._dashboard_epoch})
            self.assertFalse(app._command_busy)
            self.assertFalse(app._stop_busy)
            self.assertEqual(str(app.btn_run.cget('state')),'normal')
        finally:app.close()

    def test_legacy_task_status_never_claims_unobserved_formal_battle(self):
        state=state_payload(running=True)
        self.assertNotIn('正式作战',ui.workflow_summary(state)[0])
        state['status']['state']='done';state['running']=False
        self.assertIn('本轮完成',ui.workflow_summary(state)[0])

    def test_blocked_poll_does_not_delay_stop_and_repeat_click_is_deduped(self):
        server = MockServer(block_dashboard=1.2).start()
        self.addCleanup(server.stop)
        app = ui.AutoStoryDesktop(server.url, ROOT)
        try:
            app._start_worker()
            self.assertTrue(server.dashboard_seen.wait(1.0), "poll did not start")
            started = time.monotonic()
            app._post_stop()
            app._post_stop()  # repeated click must not queue a second stop
            self.assertTrue(server.stop_seen.wait(1.0), "stop did not reach server")
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.8)
            stops = [post for post in server.posts if post["path"] == "/api/stop"]
            self.assertEqual(len(stops), 1)
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
