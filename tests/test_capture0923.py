"""Independent tests for narrow screenshot-transport recovery.

Run from the AutoStory project root (or with that root on PYTHONPATH):

    python test_capture0923.py -v

The module uses the real autostory.device.Device class.  Every transport,
stop, settle, log, and image-write boundary is mocked, so no ADB process,
emulator, UI, network, worker, or project file is touched.  Screenshot bytes
are generated in memory; there are no fixture files.
"""
from __future__ import annotations

import struct
import subprocess
import unittest
from unittest.mock import MagicMock, Mock, call, patch

import cv2
import numpy as np

from autostory.common import DeviceUnavailableError
from autostory.device import Device
from autostory.motion import UnsettledFrameError

SERIAL = 'emulator-5554'
ADB = 'fake-adb'


class CaptureTransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.device = Device.__new__(Device)
        self.device.adb = ADB
        self.device.serial = SERIAL
        self.device.size = (1280, 720)
        self.device._capture_transport = 'raw'
        self.device._capture_fallback = None
        self.device._needs_settle = False
        self.device._settle_rois = ()
        self.device._settle_kind = 'fixed'
        self.invalidate = Mock(wraps=self.device.invalidate_frame)
        self.device.invalidate_frame = self.invalidate

        self.image = np.zeros((720, 1280, 3), np.uint8)
        self.image[40:560, 80:1100] = [12, 140, 240]
        self.png = cv2.imencode('.png', self.image)[1].tobytes()
        self.raw_frame = (
            struct.pack('<4I', 1280, 720, 1, 1)
            + cv2.cvtColor(self.image, cv2.COLOR_BGR2RGBA).tobytes()
        )

        self.stop_patch = patch('autostory.device.check_stop')
        self.check_stop = self.stop_patch.start()
        self.addCleanup(self.stop_patch.stop)
        self.wait_patch = patch('autostory.device.STOP_WAIT')
        self.wait = self.wait_patch.start()
        self.addCleanup(self.wait_patch.stop)
        self.log_patch = patch('autostory.device.log')
        self.log = self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def _closed(self, stderr='error: closed', returncode=1):
        error = DeviceUnavailableError('adb operation failed')
        error.adb_stderr = stderr
        error.adb_returncode = returncode
        return error

    def _screencap_calls(self):
        return [c for c in self.device.raw.call_args_list if 'screencap' in c.args]

    def _getstate_calls(self):
        return [c for c in self.device.raw.call_args_list if 'get-state' in c.args]

    # -- exact closed/probe/repeat transport contract ---------------------

    def test_closed_raw_recovers_with_exact_probe_and_repeat_sequence(self):
        rois = ((10, 20, 30, 40),)
        self.device._settle_rois = rois
        self.device._settle_kind = 'map'
        self.device._capture_recovery_generation = 4
        self.device.raw = Mock(side_effect=[self._closed(), b'  device\r\n', b'raw-frame'])

        data = self.device._capture_bytes()

        self.assertEqual(data, b'raw-frame')
        self.assertEqual(self.device.raw.call_args_list, [
            call('-s', SERIAL, 'exec-out', 'screencap', timeout=20),
            call('-s', SERIAL, 'get-state', timeout=4),
            call('-s', SERIAL, 'exec-out', 'screencap', timeout=20),
        ])
        self.assertEqual(self.device._capture_recovery_generation, 5)
        self.invalidate.assert_called_once()
        args, kwargs = self.invalidate.call_args
        passed_rois = kwargs.get('rois', args[0] if args else None)
        passed_kind = kwargs.get('kind', args[1] if len(args) > 1 else 'fixed')
        self.assertEqual(passed_rois, rois)
        self.assertEqual(passed_kind, 'map')
        self.assertTrue(self.device._needs_settle)
        self.assertEqual(self.device._settle_rois, rois)
        self.assertEqual(self.device._settle_kind, 'map')
        self.assertTrue(self.wait.called)
        self.assertEqual(self.wait.call_args, call(.2))
        self.log.assert_called()

    def test_recovery_checks_stop_before_probe_and_waits_before_repeat(self):
        events = []

        def raw_side_effect(*args, **kwargs):
            if 'get-state' in args:
                events.append('probe')
                return b'device'
            if 'screencap' in args:
                if 'capture1' not in events:
                    events.append('capture1')
                    raise self._closed()
                events.append('capture2')
                return self.raw_frame
            raise AssertionError(args)

        def wait_side_effect(seconds):
            events.append(f'wait:{seconds}')

        def stop_side_effect():
            events.append('stop')

        def log_side_effect(message):
            events.append('log')

        original_invalidate = self.device.invalidate_frame
        def invalidate_side_effect(*args, **kwargs):
            events.append('invalidate')
            return original_invalidate(*args, **kwargs)

        self.device.raw = Mock(side_effect=raw_side_effect)
        self.wait.side_effect = wait_side_effect
        self.check_stop.side_effect = stop_side_effect
        self.log.side_effect = log_side_effect
        self.device.invalidate_frame = Mock(side_effect=invalidate_side_effect)

        self.assertEqual(self.device._capture_bytes(), self.raw_frame)
        first_stop = events.index('stop')
        invalidate = events.index('invalidate')
        probe = events.index('probe')
        wait = events.index('wait:0.2')
        second_stop = events.index('stop', first_stop + 1)
        capture2 = events.index('capture2')
        self.assertLess(first_stop, invalidate)
        self.assertLess(invalidate, probe)
        self.assertLess(probe, wait)
        self.assertLess(wait, second_stop)
        self.assertLess(second_stop, capture2)
        self.assertLess(events.index('log'), probe)

    def test_closed_png_retries_once_with_png_flag_and_same_timeout(self):
        self.device.raw = Mock(side_effect=[self._closed(), b'device', b'png-frame'])

        data = self.device._capture_bytes(png=True, timeout=8)

        self.assertEqual(data, b'png-frame')
        self.assertEqual(self.device.raw.call_args_list, [
            call('-s', SERIAL, 'exec-out', 'screencap', '-p', timeout=8),
            call('-s', SERIAL, 'get-state', timeout=4),
            call('-s', SERIAL, 'exec-out', 'screencap', '-p', timeout=8),
        ])
        self.assertEqual(self.device._capture_recovery_generation, 1)
        self.wait.assert_called_once_with(.2)

    # -- only the exact stripped stderr is a recovery trigger -------------

    def test_non_exact_or_absent_stderr_never_retries(self):
        cases = (
            ('offline', 'error: offline'),
            ('unauthorized', 'error: unauthorized'),
            ('generic closed', 'closed'),
            ('leading space', ' error: closed'),
            ('trailing space', 'error: closed '),
            ('suffix', 'error: closed: extra'),
        )
        for label, stderr in cases:
            with self.subTest(label=label):
                error = self._closed(stderr)
                self.device.raw = Mock(side_effect=error)
                self.device._capture_recovery_generation = 7
                self.invalidate.reset_mock()
                self.wait.reset_mock()
                self.log.reset_mock()
                self.check_stop.reset_mock()

                with self.assertRaises(DeviceUnavailableError) as caught:
                    self.device._capture_bytes()

                self.assertIs(caught.exception, error)
                self.device.raw.assert_called_once_with(
                    '-s', SERIAL, 'exec-out', 'screencap', timeout=20)
                self.assertEqual(self.device._capture_recovery_generation, 7)
                self.invalidate.assert_not_called()
                self.wait.assert_not_called()

        with self.subTest(label='missing attribute'):
            error = DeviceUnavailableError('offline')
            self.device.raw = Mock(side_effect=error)
            with self.assertRaises(DeviceUnavailableError) as caught:
                self.device._capture_bytes()
            self.assertIs(caught.exception, error)
            self.device.raw.assert_called_once()

    # -- failed probe / wrong state / failed repeat ------------------------

    def test_probe_failure_propagates_without_a_second_capture(self):
        probe_error = DeviceUnavailableError('probe failed')
        self.device.raw = Mock(side_effect=[self._closed(), probe_error])

        with self.assertRaises(DeviceUnavailableError) as caught:
            self.device._capture_bytes()

        self.assertIs(caught.exception, probe_error)
        self.assertEqual(len(self._screencap_calls()), 1)
        self.assertEqual(len(self._getstate_calls()), 1)
        self.invalidate.assert_called_once()
        self.wait.assert_not_called()
        self.assertEqual(getattr(self.device, '_capture_recovery_generation', 0), 0)

    def test_wrong_probe_state_stops_without_a_second_capture(self):
        self.device.raw = Mock(side_effect=[self._closed(), b'offline\n'])

        with self.assertRaises(DeviceUnavailableError) as caught:
            self.device._capture_bytes()

        self.assertIn('offline', str(caught.exception))
        self.assertEqual(len(self._screencap_calls()), 1)
        self.assertEqual(len(self._getstate_calls()), 1)
        self.invalidate.assert_called_once()
        self.wait.assert_not_called()
        self.assertEqual(getattr(self.device, '_capture_recovery_generation', 0), 0)

    def test_failed_repeat_is_limited_to_two_captures(self):
        repeat_error = self._closed()
        self.device.raw = Mock(side_effect=[self._closed(), b'device', repeat_error])

        with self.assertRaises(DeviceUnavailableError) as caught:
            self.device._capture_bytes()

        self.assertIs(caught.exception, repeat_error)
        self.assertEqual(len(self._screencap_calls()), 2)
        self.assertEqual(len(self._getstate_calls()), 1)
        self.wait.assert_called_once_with(.2)
        self.invalidate.assert_called_once()
        self.assertEqual(getattr(self.device, '_capture_recovery_generation', 0), 0)

    # -- user stop during recovery ----------------------------------------

    def test_stop_before_probe_sends_no_further_command(self):
        self.device.raw = Mock(side_effect=self._closed())

        def stop_after_capture():
            if self.device.raw.call_count:
                raise InterruptedError('stopped')

        self.check_stop.side_effect = stop_after_capture
        with self.assertRaises(InterruptedError):
            self.device._capture_bytes()

        self.device.raw.assert_called_once()
        self.invalidate.assert_not_called()
        self.log.assert_not_called()
        self.wait.assert_not_called()

    def test_stop_after_probe_sends_no_repeat_capture(self):
        self.device.raw = Mock(side_effect=[self._closed(), b'device'])

        def stop_after_probe():
            if self.device.raw.call_count >= 2:
                raise InterruptedError('stopped')

        self.check_stop.side_effect = stop_after_probe
        with self.assertRaises(InterruptedError):
            self.device._capture_bytes()

        self.assertEqual(len(self._screencap_calls()), 1)
        self.assertEqual(len(self._getstate_calls()), 1)
        self.wait.assert_called_once_with(.2)
        self.assertEqual(getattr(self.device, '_capture_recovery_generation', 0), 0)

    # -- input commands never use the screenshot recovery path ------------

    def test_tap_swipe_back_error_closed_are_never_replayed(self):
        cases = (
            ('tap', (100, 200), {}, ('-s', SERIAL, 'shell', 'input', 'tap', 100, 200)),
            ('swipe', (10, 20, 30, 40), {'duration': 450},
             ('-s', SERIAL, 'shell', 'input', 'swipe', 10, 20, 30, 40, 450)),
            ('back', (), {}, ('-s', SERIAL, 'shell', 'input', 'keyevent', 4)),
        )
        for name, args, kwargs, expected in cases:
            with self.subTest(action=name):
                self.device.raw = Mock(side_effect=self._closed())
                self.check_stop.reset_mock()
                self.wait.reset_mock()
                self.log.reset_mock()
                with patch('autostory.device.assert_gui_idle'):
                    with self.assertRaises(DeviceUnavailableError):
                        getattr(self.device, name)(*args, **kwargs)

                self.assertEqual(self.device.raw.call_count, 1)
                self.assertEqual(self.device.raw.call_args, call(*expected))
                for forbidden in ('get-state', 'screencap', 'connect',
                                  'disconnect', 'kill-server'):
                    self.assertNotIn(forbidden, self.device.raw.call_args.args)
                self.wait.assert_not_called()
                self.log.assert_not_called()

    # -- decoded image still valid after a recovered _capture -------------

    def test_capture_returns_decoded_raw_frame_after_recovery(self):
        self.device.raw = Mock(side_effect=[self._closed(), b'device', self.raw_frame])

        image = self.device._capture()

        np.testing.assert_array_equal(image, self.image)
        self.assertEqual(self.device._capture_transport, 'raw')
        self.assertEqual(self.device.last_capture['transport'], 'raw')
        self.assertEqual(self.device.last_capture['size'], [1280, 720])
        self.assertEqual(self.device.last_capture['bytes'], len(self.raw_frame))
        self.assertIsNone(self.device.last_capture['fallback'])
        self.assertEqual(len(self._screencap_calls()), 2)
        self.assertEqual(len(self._getstate_calls()), 1)
        self.assertEqual(self.device._capture_recovery_generation, 1)

    def test_capture_returns_decoded_png_frame_after_recovery(self):
        self.device._capture_transport = 'png'
        self.device.raw = Mock(side_effect=[self._closed(), b'device', self.png])

        image = self.device._capture()

        np.testing.assert_array_equal(image, self.image)
        self.assertEqual(self.device._capture_transport, 'png')
        self.assertEqual(self.device.last_capture['transport'], 'png')
        self.assertEqual(self.device.last_capture['size'], [1280, 720])
        self.assertEqual(self.device.last_capture['bytes'], len(self.png))
        self.assertEqual(self.device.raw.call_args_list, [
            call('-s', SERIAL, 'exec-out', 'screencap', '-p', timeout=8),
            call('-s', SERIAL, 'get-state', timeout=4),
            call('-s', SERIAL, 'exec-out', 'screencap', '-p', timeout=8),
        ])
        self.assertEqual(self.device._capture_recovery_generation, 1)

    def test_existing_timeout_raw_to_png_fallback_is_still_one_retry(self):
        timeout_error = DeviceUnavailableError('raw timeout')
        timeout_error.__cause__ = subprocess.TimeoutExpired('adb', 20)
        self.device._capture_transport = 'raw'

        with patch.object(self.device, '_capture_bytes',
                          side_effect=[timeout_error, self.png]) as capture_bytes:
            image = self.device._capture()

        np.testing.assert_array_equal(image, self.image)
        self.assertEqual(capture_bytes.call_args_list, [call(), call(png=True, timeout=8)])
        self.assertEqual(self.device._capture_transport, 'png')
        self.assertEqual(self.device.last_capture['transport'], 'png')
        self.assertTrue(self.device.last_capture['fallback'])

    def test_closed_transport_error_does_not_trigger_png_fallback(self):
        # A closed stream that survived _capture_bytes' single recovery is not
        # the existing timeout fallback trigger; _capture must not switch PNG.
        error = self._closed()
        with patch.object(self.device, '_capture_bytes',
                          side_effect=error) as capture_bytes:
            with self.assertRaises(DeviceUnavailableError) as caught:
                self.device._capture()

        self.assertIs(caught.exception, error)
        capture_bytes.assert_called_once()
        self.assertEqual(self.device._capture_transport, 'raw')

    # -- raw metadata and no replay inside raw ----------------------------

    def test_raw_attaches_trimmed_stderr_and_returncode_without_retry(self):
        result = Mock(returncode=7, stderr=b'  error: closed \r\n', stdout=b'')
        with patch('autostory.device.run_command', return_value=result) as run_command:
            with self.assertRaises(DeviceUnavailableError) as caught:
                self.device.raw('-s', SERIAL, 'shell', 'input', 'tap')

        error = caught.exception
        self.assertEqual(error.adb_stderr, 'error: closed')
        self.assertEqual(error.adb_returncode, 7)
        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0],
                         [ADB, '-s', SERIAL, 'shell', 'input', 'tap'])
        self.assertEqual(run_command.call_args.kwargs['timeout'], 20)

    def test_raw_success_returns_stdout_unmodified(self):
        result = Mock(returncode=0, stderr=b'', stdout=b'device\n')
        with patch('autostory.device.run_command', return_value=result):
            self.assertEqual(self.device.raw('get-state'), b'device\n')

    def test_real_raw_metadata_drives_one_closed_recovery(self):
        closed = Mock(returncode=1, stderr=b'error: closed\n', stdout=b'')
        probe = Mock(returncode=0, stderr=b'', stdout=b'device\n')
        repeat = Mock(returncode=0, stderr=b'', stdout=b'recovered-frame')
        with patch('autostory.device.run_command',
                   side_effect=[closed, probe, repeat]) as run_command:
            data = self.device._capture_bytes()

        self.assertEqual(data, b'recovered-frame')
        self.assertEqual(run_command.call_count, 3)
        self.assertEqual(
            [c.args[0] for c in run_command.call_args_list],
            [
                [ADB, '-s', SERIAL, 'exec-out', 'screencap'],
                [ADB, '-s', SERIAL, 'get-state'],
                [ADB, '-s', SERIAL, 'exec-out', 'screencap'],
            ])
        self.assertEqual(
            [c.kwargs['timeout'] for c in run_command.call_args_list],
            [20, 4, 20])
        self.assertEqual(self.device._capture_recovery_generation, 1)
        self.wait.assert_called_once_with(.2)

    # -- one fresh settling window, never a third -------------------------

    def test_stable_shot_refreshes_exactly_once_after_recovery(self):
        calls = []

        def fake_settle(capture, stop, wait, *, rois=(), kind='fixed'):
            calls.append((capture, stop, wait, rois, kind))
            if len(calls) == 1:
                self.device._capture_recovery_generation = 1
                return self.image, {'confirmed': False, 'frames': 5}
            return self.image, {'confirmed': True, 'frames': 3}

        self.device._needs_settle = True
        self.device._settle_rois = ((10, 20, 30, 40),)
        self.device._settle_kind = 'map'
        self.device._capture_transport = 'raw'

        with patch('autostory.motion.settle', side_effect=fake_settle):
            image = self.device.stable_shot()

        self.assertIs(image, self.image)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][3:], (((10, 20, 30, 40),), 'map'))
        self.assertEqual(calls[1][3:], (((10, 20, 30, 40),), 'map'))
        self.assertTrue(self.device.last_settle['confirmed'])
        self.assertFalse(self.device._needs_settle)
        self.assertTrue(self.check_stop.called)

    def test_stable_shot_never_opens_a_third_settle_window(self):
        calls = []

        def fake_settle(capture, stop, wait, *, rois=(), kind='fixed'):
            calls.append(rois)
            self.device._capture_recovery_generation = (
                getattr(self.device, '_capture_recovery_generation', 0) + 1)
            return self.image, {'confirmed': False, 'frames': 5}

        self.device._needs_settle = True
        buffer = Mock()
        with patch('autostory.motion.settle', side_effect=fake_settle), \
             patch('autostory.device.DATA', MagicMock()), \
             patch('autostory.device.cv2.imencode', return_value=(True, buffer)), \
             patch('autostory.common.write_json'):
            with self.assertRaises(UnsettledFrameError):
                self.device.stable_shot()

        self.assertEqual(len(calls), 2)
        buffer.tofile.assert_called_once()

    def test_stable_shot_does_not_refresh_without_recovery_or_transport_fallback(self):
        calls = []

        def fake_settle(capture, stop, wait, *, rois=(), kind='fixed'):
            calls.append(rois)
            return self.image, {'confirmed': False, 'frames': 5}

        self.device._needs_settle = True
        buffer = Mock()
        with patch('autostory.motion.settle', side_effect=fake_settle), \
             patch('autostory.device.DATA', MagicMock()), \
             patch('autostory.device.cv2.imencode', return_value=(True, buffer)), \
             patch('autostory.common.write_json'):
            with self.assertRaises(UnsettledFrameError):
                self.device.stable_shot()

        self.assertEqual(len(calls), 1)
        buffer.tofile.assert_called_once()

    def test_existing_raw_to_png_settle_fallback_is_still_one_retry(self):
        calls = []

        def fake_settle(capture, stop, wait, *, rois=(), kind='fixed'):
            calls.append(rois)
            if len(calls) == 1:
                self.device._capture_transport = 'png'
                return self.image, {'confirmed': False, 'frames': 5}
            return self.image, {'confirmed': True, 'frames': 3}

        self.device._needs_settle = True
        self.device._capture_transport = 'raw'
        with patch('autostory.motion.settle', side_effect=fake_settle):
            image = self.device.stable_shot()

        self.assertIs(image, self.image)
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.device.last_settle['confirmed'])
        self.assertFalse(self.device._needs_settle)


if __name__ == '__main__':
    unittest.main()
