"""Offline tests for worker lock ownership, claim order and STOP preservation."""
from __future__ import annotations

import json
import os
import time
import unittest

from runtime_support import (
    clean_runtime, finish_worker, make_project, read_json, remove_project,
    run_worker, start_lock_holder, start_worker, stop_lock_holder, wait_for,
)


class WorkerLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make_project()
        cls.runtime = cls.root / 'runtime'

    @classmethod
    def tearDownClass(cls):
        remove_project(cls.root)

    def setUp(self):
        clean_runtime(self.root)

    def test_busy_worker_does_not_claim_status_touch_stop_or_build_device(self):
        (self.runtime / 'STOP').write_text('keep-me', encoding='utf-8')
        marker = self.runtime / 'device.marker'
        holder, release = start_lock_holder(self.root)
        self.addCleanup(stop_lock_holder, holder, release)
        held_claim = read_json(self.runtime / 'worker.json', {})

        code = run_worker(
            self.root, 'run',
            env={'FAKE_DEVICE_MARKER': str(marker)},
        )

        self.assertEqual(code, 3)
        self.assertFalse((self.runtime / 'status.json').exists())
        self.assertEqual(read_json(self.runtime / 'worker.json', {}), held_claim,
                         'busy worker overwrote the owner claim')
        self.assertFalse(marker.exists(), 'worker must not create Device while busy')
        self.assertEqual((self.runtime / 'STOP').read_text(encoding='utf-8'), 'keep-me')

    def test_plan_is_serialized_and_loser_cannot_erase_new_stop(self):
        marker = self.runtime / 'snapshot.json'
        env = {'FAKE_CALL_MARKER': str(marker), 'FAKE_PLAN_SECONDS': '1.0'}
        first = start_worker(self.root, 'plan', env=env)
        self.addCleanup(finish_worker, first)
        self.assertTrue(wait_for(marker), 'first worker did not enter plan')

        running = read_json(self.runtime / 'status.json', {})
        self.assertEqual(running.get('state'), 'running')
        self.assertEqual(running.get('pid'), first.pid)
        first_run_id = running.get('run_id')
        self.assertTrue(first_run_id)
        self.assertTrue((self.runtime / 'worker.json').exists())

        # A stop request arrives while plan is running.  The rejected second
        # worker must not delete it and must not overwrite the first status.
        (self.runtime / 'STOP').write_text('mid-run', encoding='utf-8')
        second_code = run_worker(self.root, 'plan')
        self.assertEqual(second_code, 3)
        self.assertTrue((self.runtime / 'STOP').exists(), 'loser erased STOP')
        still = read_json(self.runtime / 'status.json', {})
        self.assertEqual(still.get('run_id'), first_run_id)
        self.assertEqual(still.get('state'), 'running')

        self.assertEqual(finish_worker(first), 0)
        final = read_json(self.runtime / 'status.json', {})
        self.assertEqual(final.get('state'), 'done')
        self.assertEqual(final.get('run_id'), first_run_id)
        self.assertEqual(final.get('pid'), first.pid)
        self.assertFalse((self.runtime / 'worker.json').exists())
        # Fake plan had already passed its check_stop before the mid-run STOP;
        # the worker itself never deletes the marker.
        self.assertTrue((self.runtime / 'STOP').exists())

    def test_old_stop_is_ignored_without_unlink(self):
        stop = self.runtime / 'STOP'
        stop.write_text('previous-stop', encoding='utf-8')
        old_ns = time.time_ns() - 1_000_000_000
        os.utime(stop, ns=(old_ns, old_ns))
        marker = self.runtime / 'snapshot.json'
        code = run_worker(self.root, 'plan', env={'FAKE_CALL_MARKER': str(marker)})
        self.assertEqual(code, 0)

        snapshot = json.loads(marker.read_text(encoding='utf-8'))
        self.assertEqual(snapshot['tag'], 'plan')
        self.assertTrue(snapshot['stop'], 'STOP file should remain on disk')
        self.assertFalse(snapshot['stop_requested'], 'old STOP must be logically reset')
        self.assertIsNotNone(snapshot['claim'], 'claim file must exist while worker runs')
        status = snapshot['status']
        self.assertEqual(status.get('state'), 'running')
        self.assertEqual(status.get('pid'), snapshot['pid'])
        self.assertIsInstance(status.get('pid_created'), int)
        self.assertIsInstance(status.get('accepted_ns'), int)
        self.assertEqual(snapshot['claim'].get('run_id'), status.get('run_id'))
        self.assertEqual(snapshot['claim'].get('pid'), status.get('pid'))

        final = read_json(self.runtime / 'status.json', {})
        self.assertEqual(final.get('state'), 'done')
        self.assertEqual(final.get('run_id'), status.get('run_id'))
        self.assertEqual(final.get('pid'), snapshot['pid'])
        self.assertTrue((self.runtime / 'plan-called.json').exists())
        self.assertTrue(stop.exists(), 'worker must not unlink STOP')
        self.assertFalse((self.runtime / 'worker.json').exists())

    def test_stop_after_accepted_ns_is_honoured_and_preserved(self):
        accepted_ns = time.time_ns() - 5_000_000
        stop = self.runtime / 'STOP'
        stop.write_text('after-accept', encoding='utf-8')
        stop_ns = max(time.time_ns(), accepted_ns + 1_000_000)
        os.utime(stop, ns=(stop_ns, stop_ns))
        marker = self.runtime / 'snapshot.json'
        code = run_worker(self.root, 'plan', accepted_ns=accepted_ns,
                          env={'FAKE_CALL_MARKER': str(marker)})
        self.assertEqual(code, 1)
        final = read_json(self.runtime / 'status.json', {})
        self.assertEqual(final.get('state'), 'stopped')
        self.assertEqual(final.get('accepted_ns'), accepted_ns)
        self.assertTrue(stop.exists(), 'STOP after acceptance must not be deleted')
        self.assertFalse(marker.exists(), 'plan must not start after a later STOP')
        self.assertFalse((self.runtime / 'plan-called.json').exists())
        self.assertFalse((self.runtime / 'worker.json').exists())

    def test_error_status_keeps_identity_and_releases_lock(self):
        code = run_worker(self.root, 'plan', env={'FAKE_PLAN_FAIL': '1'})
        self.assertEqual(code, 1)
        status = read_json(self.runtime / 'status.json', {})
        self.assertEqual(status.get('state'), 'error')
        self.assertTrue(status.get('run_id'))
        self.assertTrue(status.get('pid'))
        self.assertFalse((self.runtime / 'worker.json').exists())

        # The failed run must not leave the all-task lock held forever.
        self.assertEqual(run_worker(self.root, 'plan'), 0)

    def test_simultaneous_workers_have_exactly_one_winner(self):
        marker_a = self.runtime / 'snapshot-a.json'
        marker_b = self.runtime / 'snapshot-b.json'
        env_a = {'FAKE_CALL_MARKER': str(marker_a), 'FAKE_PLAN_SECONDS': '1.5'}
        env_b = {'FAKE_CALL_MARKER': str(marker_b), 'FAKE_PLAN_SECONDS': '1.5'}
        first = start_worker(self.root, 'plan', env=env_a)
        second = start_worker(self.root, 'plan', env=env_b)
        self.addCleanup(finish_worker, first)
        self.addCleanup(finish_worker, second)

        codes = sorted([finish_worker(first, timeout=20), finish_worker(second, timeout=20)])
        self.assertEqual(codes, [0, 3])
        winner_marker = marker_a if first.returncode == 0 else marker_b
        loser_marker = marker_b if winner_marker == marker_a else marker_a
        self.assertTrue(winner_marker.exists())
        self.assertFalse(loser_marker.exists(), 'loser entered plan without the lock')

        final = read_json(self.runtime / 'status.json', {})
        winner = json.loads(winner_marker.read_text(encoding='utf-8'))
        self.assertEqual(final.get('state'), 'done')
        self.assertEqual(final.get('pid'), winner['pid'])
        self.assertEqual(final.get('run_id'), winner['claim']['run_id'])
        self.assertFalse((self.runtime / 'worker.json').exists())


if __name__ == '__main__':
    unittest.main()
