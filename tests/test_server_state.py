"""Offline HTTP tests for status reconciliation, busy handling and validation."""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

from runtime_support import (
    CREATE_NO_WINDOW, clean_runtime, make_project, read_json, remove_project,
    run_worker, start_lock_holder, start_sleeper, stop_lock_holder, stop_process,
    wait_until,
)


def load_server(root):
    for name in list(sys.modules):
        if name == 'autostory' or name.startswith('autostory.') or name == 'candidate_server':
            del sys.modules[name]
    sys.path.insert(0, str(root))
    try:
        spec = importlib.util.spec_from_file_location('candidate_server', root / 'server.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules['candidate_server'] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(root))
    return module


class BufferedTestConnection(http.client.HTTPConnection):
    """Send each small test request together, including intentionally bad headers.

    On Windows an early header rejection can close the socket between
    http.client's separate header/body sends. Coalescing the test request avoids
    that transport race; the real HTTP handler and response assertions remain.
    No requests are retried and rejected requests still contain their bodies.
    """
    def request(self,*args,**kwargs):
        self._request_chunks=[]
        try:
            super().request(*args,**kwargs)
            wire=b''.join(self._request_chunks)
        finally:
            del self._request_chunks
        super().send(wire)

    def send(self,data):
        if hasattr(self,'_request_chunks'):
            self._request_chunks.append(data)
        else:super().send(data)


class ServerStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_modules={name:module for name,module in sys.modules.items()
                              if name=='autostory' or name.startswith('autostory.') or name=='candidate_server'}
        cls.addClassCleanup(cls.restore_imports)
        cls.root = make_project()
        cls.runtime = cls.root / 'runtime'
        cls.server_module = load_server(cls.root)
        import autostory.runtime_state as runtime_state
        cls.runtime_state = runtime_state
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), cls.server_module.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def restore_imports(cls):
        # The isolated server fixture must not leak its stub package into later
        # OCR/navigation tests in unittest discovery.
        for name in list(sys.modules):
            if name=='autostory' or name.startswith('autostory.') or name=='candidate_server':
                del sys.modules[name]
        sys.modules.update(cls.original_modules)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=5)
        cls.httpd.server_close()
        remove_project(cls.root)

    def setUp(self):
        clean_runtime(self.root)
        self.server_module.PROCESS = None

    def tearDown(self):
        proc = self.server_module.PROCESS
        if proc is not None:
            wait = getattr(proc, 'wait', None)
            if wait is not None:
                try:
                    wait(timeout=15)
                except Exception:
                    pass
        self.server_module.PROCESS = None

    # --- helpers ---------------------------------------------------------
    def request(self, method, path, body=None, *, token=True, length=None,
                headers=None):
        conn = BufferedTestConnection('127.0.0.1', self.port, timeout=8)
        hdrs = {'Connection': 'close'}
        if token:
            hdrs['X-AutoStory-Token'] = self.server_module.TOKEN
        else:
            hdrs['X-AutoStory-Token'] = 'wrong-token'
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            hdrs['Content-Type'] = 'application/json'
            hdrs['Content-Length'] = str(len(payload)) if length is None else str(length)
        else:
            payload = None
        hdrs.update(headers or {})
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            response = conn.getresponse()
            raw = response.read()
            status = response.status
        finally:
            conn.close()
        try:
            decoded = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            decoded = raw
        return status, decoded

    def get_state(self):
        status, data = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        return data

    def test_rebinding_host_cannot_read_page_token_or_health(self):
        for path in ('/', '/api/health', '/api/state'):
            for host in ('attacker.example:'+str(self.port),'127.0.0.1.evil:'+str(self.port),
                         '127.0.0.1:1','localhost.:'+str(self.port)):
                code,data=self.request('GET',path,headers={'Host':host})
                self.assertEqual(code,403,(host,path));self.assertNotIn(self.server_module.TOKEN,str(data))

    def test_cross_origin_stop_cannot_mutate_runtime_even_with_valid_token(self):
        for origin in ('null','https://evil.example','http://127.0.0.1:1',f'http://localhost:{self.port}',
                       f'http://127.0.0.1:{self.port}/'):
            code,_=self.request('POST','/api/stop',{},headers={'Origin':origin})
            self.assertEqual(code,403,origin);self.assertFalse((self.runtime/'STOP').exists())

    def test_matching_origin_and_cli_without_origin_remain_usable(self):
        for host in (f'127.0.0.1:{self.port}',f'localhost:{self.port}'):
            code,_=self.request('POST','/api/stop',{},headers={'Host':host,'Origin':'http://'+host})
            self.assertEqual(code,200)
        code,_=self.request('POST','/api/stop',{})
        self.assertEqual(code,200)

    def test_duplicate_host_or_origin_cannot_expose_token(self):
        for duplicate in ('Host','Origin'):
            conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=8)
            conn.putrequest('GET','/',skip_host=True)
            host=f'127.0.0.1:{self.port}'
            conn.putheader('Host',host)
            conn.putheader('Origin','http://'+host)
            conn.putheader(duplicate,host if duplicate=='Host' else 'http://'+host)
            conn.endheaders();response=conn.getresponse()
            self.assertEqual(response.status,403);response.read();conn.close()

    def test_dashboard_revision_omits_unchanged_coverage_and_refreshes_changes(self):
        code,first=self.request('GET','/api/dashboard')
        self.assertEqual(code,200);self.assertIsInstance(first['coverage'],dict)
        code,second=self.request('GET','/api/dashboard?revision='+first['revision'])
        self.assertEqual(code,200);self.assertIsNone(second['coverage'])
        self.assertIn('workflow',second['state'])
        (self.runtime/'progress.json').write_text('{"x":{"code":"DV-1","state":"cleared"}}')
        code,third=self.request('GET','/api/dashboard?revision='+first['revision'])
        self.assertIsInstance(third['coverage'],dict)
        self.assertNotEqual(first['revision'],third['revision'])

    def test_dashboard_does_not_show_another_runs_workflow(self):
        self.write_status({'state':'done','run_id':'new','chapter':'CW-EX'})
        (self.runtime/'workflow.json').write_text('{"run_id":"old","code":"DV-1","phase":"formal"}')
        data=self.get_state()
        self.assertEqual(data['workflow']['code'],'')
        self.assertNotEqual(data['workflow']['phase'],'formal')

    def write_status(self, value):
        (self.runtime / 'status.json').write_text(
            json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def write_claim(self, value):
        (self.runtime / 'worker.json').write_text(
            json.dumps(value, ensure_ascii=False), encoding='utf-8')

    # --- status / PID identity ------------------------------------------
    def test_completed_pid_status_is_interrupted(self):
        proc = subprocess.Popen([sys.executable, '-c', 'pass'],
                                creationflags=CREATE_NO_WINDOW)
        pid = proc.pid
        proc.wait(timeout=10)
        self.write_status({
            'state': 'running', 'action': 'plan', 'chapter': 'DV',
            'run_id': 'completed-run', 'pid': pid, 'pid_created': 1,
        })
        data = self.get_state()
        self.assertFalse(data['running'])
        self.assertEqual(data['status']['state'], 'interrupted')
        self.assertEqual(data['status']['pid'], pid)

    def test_live_unrelated_pid_is_not_running_without_claim(self):
        sleeper = start_sleeper()
        self.addCleanup(stop_process, sleeper)
        marker = self.runtime_state.process_creation_marker(sleeper.pid)
        self.assertIsNotNone(marker)
        self.write_status({
            'state': 'running', 'action': 'run', 'chapter': 'DV',
            'run_id': 'unrelated-live', 'pid': sleeper.pid, 'pid_created': marker,
        })
        data = self.get_state()
        self.assertFalse(data['running'])
        self.assertEqual(data['status']['state'], 'interrupted')

    def test_dead_claim_does_not_keep_status_running(self):
        proc = subprocess.Popen([sys.executable, '-c', 'pass'],
                                creationflags=CREATE_NO_WINDOW)
        pid = proc.pid
        proc.wait(timeout=10)
        identity = {
            'run_id': 'dead-claim', 'pid': pid, 'pid_created': 1,
            'action': 'plan', 'chapter': 'DV', 'started': '2026-10-30T00:00:00+08:00',
        }
        self.write_claim(identity)
        self.write_status({**identity, 'state': 'running'})
        data = self.get_state()
        self.assertFalse(data['running'])
        self.assertEqual(data['status']['state'], 'interrupted')

    def test_claim_with_mismatched_creation_marker_is_not_running(self):
        identity = self.runtime_state.make_identity('plan', 'DV')
        self.assertIsInstance(identity['pid_created'], int)
        identity['pid_created'] += 1  # same live PID, different process birth
        self.write_claim(identity)
        self.write_status({**identity, 'state': 'running'})
        data = self.get_state()
        self.assertFalse(data['running'])
        self.assertEqual(data['status']['state'], 'interrupted')

    def test_live_claim_beats_completed_local_server_process(self):
        lock = self.runtime_state.TaskLock(self.runtime / 'alltasks.lock')
        self.assertTrue(lock.acquire())
        try:
            identity = self.runtime_state.make_identity('plan', 'DV')
            self.runtime_state.write_claim(self.runtime, identity)
            self.write_status({**identity, 'state': 'running'})

            class CompletedProcess:
                def poll(self):
                    return 0

            self.server_module.PROCESS = CompletedProcess()
            data = self.get_state()
            self.assertTrue(data['running'], 'unrelated completed PROCESS marked live run interrupted')
            self.assertEqual(data['status']['state'], 'running')
            self.assertEqual(data['status']['pid'], os.getpid())
            self.assertEqual(data['status']['run_id'], identity['run_id'])
        finally:
            self.runtime_state.clear_claim(self.runtime)
            lock.release()

    def test_stale_running_status_without_claim_is_interrupted(self):
        self.write_status({'state': 'running', 'action': 'scan', 'chapter': 'DV',
                           'run_id': 'no-claim'})
        data = self.get_state()
        self.assertFalse(data['running'])
        self.assertEqual(data['status']['state'], 'interrupted')

    def test_live_claim_replaces_old_status_during_worker_startup(self):
        lock = self.runtime_state.TaskLock(self.runtime / 'alltasks.lock')
        self.assertTrue(lock.acquire())
        try:
            identity = self.runtime_state.make_identity('plan', 'DV')
            self.runtime_state.write_claim(self.runtime, identity)
            self.write_status({'state': 'done', 'action': 'scan', 'chapter': 'CW',
                               'run_id': 'previous-run'})
            data = self.get_state()
            self.assertTrue(data['running'])
            self.assertEqual(data['status']['state'], 'running')
            self.assertEqual(data['status']['run_id'], identity['run_id'])
            self.assertEqual(data['status']['pid'], os.getpid())
            self.assertEqual(data['status']['action'], 'plan')
        finally:
            self.runtime_state.clear_claim(self.runtime)
            lock.release()

    def test_final_status_with_live_claim_is_not_running(self):
        lock = self.runtime_state.TaskLock(self.runtime / 'alltasks.lock')
        self.assertTrue(lock.acquire())
        try:
            identity = self.runtime_state.make_identity('plan', 'DV')
            self.runtime_state.write_claim(self.runtime, identity)
            self.write_status({**identity, 'state': 'done'})
            data = self.get_state()
            self.assertFalse(data['running'])
            self.assertEqual(data['status']['state'], 'done')
        finally:
            self.runtime_state.clear_claim(self.runtime)
            lock.release()

    # --- HTTP busy / STOP preservation ---------------------------------
    def test_busy_action_returns_409_and_preserves_stop_and_status(self):
        (self.runtime / 'STOP').write_text('do-not-erase', encoding='utf-8')
        self.write_status({'state': 'done', 'run_id': 'previous', 'action': 'plan',
                           'chapter': 'DV'})
        holder, release = start_lock_holder(self.root)
        self.addCleanup(stop_lock_holder, holder, release)

        code, data = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'DV'})

        self.assertEqual(code, 409)
        self.assertIn('已有后台任务', data['error'])
        self.assertEqual((self.runtime / 'STOP').read_text(encoding='utf-8'), 'do-not-erase')
        self.assertEqual(read_json(self.runtime / 'status.json', {})['run_id'], 'previous')
        self.assertIsNone(self.server_module.PROCESS)

    def test_end_to_end_server_worker_conflict_preserves_mid_run_stop(self):
        marker = self.runtime / 'conflict-plan-entered.json'
        release = self.runtime / 'conflict-plan.release'
        marker.unlink(missing_ok=True)
        release.unlink(missing_ok=True)
        test_env = {'FAKE_PLAN_SECONDS': '0', 'FAKE_CALL_MARKER': str(marker),
                    'FAKE_PLAN_RELEASE': str(release)}
        old_env = {key: os.environ.get(key) for key in test_env}
        os.environ.update(test_env)
        try:
            code, data = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'DV'})
            self.assertEqual(code, 200)
            first_pid = data['pid']
            self.assertTrue(
                wait_until(lambda: read_json(self.runtime / 'status.json', {}).get('state') == 'running'),
                'server-spawned worker did not claim status')
            first = read_json(self.runtime / 'status.json', {})
            self.assertEqual(first.get('pid'), first_pid)
            self.assertTrue(first.get('run_id'))
            self.assertTrue((self.runtime / 'worker.json').exists())
            self.assertTrue(wait_until(marker.exists), 'fake plan did not enter its held operation')

            (self.runtime / 'STOP').write_text('mid-run-stop', encoding='utf-8')
            code, data = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'DV'})
            self.assertEqual(code, 409)
            self.assertTrue((self.runtime / 'STOP').exists(), 'busy HTTP path erased STOP')
            release.write_text('release', encoding='utf-8')

            self.assertTrue(
                # The fake operation has already passed the initial check_stop;
                # finishing it or observing STOP afterwards are valid outcomes.
                wait_until(lambda: read_json(self.runtime / 'status.json', {}).get('state') in ('done','stopped'), timeout=15),
                'first worker did not finish')
            self.assertTrue(
                wait_until(lambda: self.server_module.PROCESS is not None
                           and self.server_module.PROCESS.poll() is not None
                           and not (self.runtime / 'worker.json').exists(), timeout=10),
                'worker process did not exit/clear claim')
            final = read_json(self.runtime / 'status.json', {})
            self.assertEqual(final.get('run_id'), first.get('run_id'))
            self.assertEqual(final.get('pid'), first_pid)
            self.assertTrue((self.runtime / 'STOP').exists())
            self.assertFalse((self.runtime / 'worker.json').exists())
        finally:
            release.write_text('release', encoding='utf-8')
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_stop_endpoint_still_writes_stop_while_lock_held(self):
        holder, release = start_lock_holder(self.root)
        self.addCleanup(stop_lock_holder, holder, release)
        code, data = self.request('POST', '/api/stop', {'action': 'stop', 'chapter': 'DV'})
        self.assertEqual(code, 200)
        self.assertTrue((self.runtime / 'STOP').exists())

    def test_successful_spawn_does_not_clear_stop_or_claim_status(self):
        (self.runtime / 'STOP').write_text('pre-existing', encoding='utf-8')
        captured = []

        class FakeProcess:
            pid = 424242

            def poll(self):
                return None

        def fake_popen(args, **kwargs):
            captured.append((args, kwargs))
            return FakeProcess()

        real_popen = self.server_module.subprocess.Popen
        self.server_module.subprocess.Popen = fake_popen
        try:
            code, data = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'DV'})
        finally:
            self.server_module.subprocess.Popen = real_popen
            self.server_module.PROCESS = None

        self.assertEqual(code, 200)
        self.assertEqual(data['pid'], 424242)
        self.assertIsInstance(data.get('accepted_ns'), int)
        self.assertTrue((self.runtime / 'STOP').exists(), 'server erased STOP before worker claim')
        self.assertFalse((self.runtime / 'status.json').exists(), 'server claimed status too early')
        self.assertTrue(captured)
        args = captured[0][0]
        self.assertIn('worker.py', args[2])
        self.assertIn('--accepted-ns', args)
        self.assertEqual(int(args[args.index('--accepted-ns') + 1]), data['accepted_ns'])

    def test_stop_after_http_acceptance_is_honoured_by_later_worker(self):
        captured = []

        class FakeProcess:
            pid = 424243

            def poll(self):
                return None

        def fake_popen(args, **kwargs):
            captured.append((args, kwargs))
            return FakeProcess()

        real_popen = self.server_module.subprocess.Popen
        self.server_module.subprocess.Popen = fake_popen
        try:
            code, data = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'DV'})
        finally:
            self.server_module.subprocess.Popen = real_popen
            self.server_module.PROCESS = None

        self.assertEqual(code, 200)
        accepted_ns = data['accepted_ns']
        self.assertIsInstance(accepted_ns, int)

        # The HTTP request has already returned 200.  The user stops before any
        # real worker exists/claims; this STOP is newer than the accepted epoch.
        stop_code, _ = self.request('POST', '/api/stop', {'action': 'stop', 'chapter': 'DV'})
        self.assertEqual(stop_code, 200)
        stop = self.runtime / 'STOP'
        self.assertTrue(stop.exists())
        self.assertGreaterEqual(stop.stat().st_mtime_ns, accepted_ns)

        marker = self.runtime / 'snapshot.json'
        worker_code = run_worker(
            self.root, 'plan', accepted_ns=accepted_ns,
            env={'FAKE_CALL_MARKER': str(marker)},
        )
        self.assertEqual(worker_code, 1)
        final = read_json(self.runtime / 'status.json', {})
        self.assertEqual(final.get('state'), 'stopped')
        self.assertEqual(final.get('accepted_ns'), accepted_ns)
        self.assertTrue(stop.exists(), 'worker discarded a STOP sent after HTTP acceptance')
        self.assertFalse(marker.exists(), 'plan ran despite a later STOP')
        self.assertFalse((self.runtime / 'plan-called.json').exists())
        self.assertFalse((self.runtime / 'worker.json').exists())

    def test_spawn_failure_returns_500_and_preserves_stop(self):
        (self.runtime / 'STOP').write_text('spawn-failure-stop', encoding='utf-8')

        def failing_popen(args, **kwargs):
            raise OSError('cannot spawn')

        real_popen = self.server_module.subprocess.Popen
        self.server_module.subprocess.Popen = failing_popen
        try:
            code, data = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'DV'})
        finally:
            self.server_module.subprocess.Popen = real_popen
            self.server_module.PROCESS = None

        self.assertEqual(code, 500)
        self.assertTrue((self.runtime / 'STOP').exists())
        self.assertFalse((self.runtime / 'status.json').exists())

    # --- request validation --------------------------------------------
    def test_malformed_json_top_level_types_are_rejected(self):
        cases = {
            'invalid': b'{',
            'list': b'[]',
            'string': b'"DV"',
            'number': b'5',
            'null': b'null',
            'boolean': b'true',
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                code, data = self.request('POST', '/api/action', body)
                self.assertEqual(code, 400)
                self.assertIn('无效请求', data['error'])
                self.assertFalse((self.runtime / 'STOP').exists())

    def test_invalid_content_length_is_rejected_without_side_effects(self):
        for name, length, expected in (
                ('non-numeric', 'abc', 400),
                ('negative', '-1', 400),
                ('too-large', '4096', 413)):
            with self.subTest(name=name):
                code, data = self.request('POST', '/api/action', b'{}', length=length)
                self.assertEqual(code, expected)
                self.assertFalse((self.runtime / 'STOP').exists())
                self.assertFalse((self.runtime / 'status.json').exists())

    def test_token_action_and_chapter_validation(self):
        code, _ = self.request('POST', '/api/action', {'action': 'plan'}, token=False)
        self.assertEqual(code, 403)
        code, _ = self.request('POST', '/api/action', {'action': 'nope', 'chapter': 'DV'})
        self.assertEqual(code, 400)
        code, _ = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 'bad prefix!'})
        self.assertEqual(code, 400)
        code, _ = self.request('POST', '/api/action', {'action': 'plan', 'chapter': 7})
        self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
