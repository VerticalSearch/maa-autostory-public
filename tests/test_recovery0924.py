"""0.9.24 regressions: unusable map anchors, bounded device recovery, scan resume UI.

Each test pins one behaviour that the release claims:

* a map anchor box that carries no label pixels must never be handed to the map
  gate (it made ``settle`` unsatisfiable and burned the whole deadline);
* a transient emulator loss may be waited out a bounded number of times, but a
  configuration/authorisation fault, a user stop or an exhausted budget still
  ends the run with the saved progress intact;
* the control page offers a resume shortcut only for a failed read-only scan.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from autostory import motion, scanning
from autostory.common import DeviceUnavailableError, is_transient_device_error, read_json, write_json
from autostory.device import Device

ROOT = Path(__file__).resolve().parents[1]
LABEL_A = (200, 300, 80, 40)
LABEL_B = (700, 500, 80, 40)
LABEL_C = (1000, 200, 80, 40)
LABELS = {'a': LABEL_A, 'b': LABEL_B, 'c': LABEL_C}


def map_frame(*filled):
    """A textured page with optional label pixels inside named anchor boxes."""
    image = np.zeros((720, 1280, 3), np.uint8)
    image[:] = 60
    image[80:420, 60:940] = 180
    for name in filled:
        x, y, w, h = LABELS[name]
        label = np.zeros((h, w, 3), np.uint8)
        label[:, ::4] = 255          # text-like strokes, not a flat block
        image[y:y + h, x:x + w] = label
    return image


class AnchorTextureTests(unittest.TestCase):
    def test_region_texture_reports_blank_boxes_as_zero(self):
        image = map_frame('a')
        self.assertGreater(motion.region_texture(image, LABEL_A), motion.TEXTURE_MIN_STD)
        self.assertLess(motion.region_texture(image, LABEL_B), motion.TEXTURE_MIN_STD)

    def test_textured_regions_drops_only_the_blank_boxes(self):
        image = map_frame('a')
        self.assertEqual(motion.textured_regions(image, [LABEL_A, LABEL_B]), (LABEL_A,))

    def test_non_image_frames_keep_the_previous_behaviour(self):
        rois = (LABEL_A, LABEL_B)
        self.assertIsNone(motion.region_texture(Mock(), LABEL_A))
        self.assertEqual(motion.textured_regions(Mock(), rois), rois)

    def test_map_settle_confirms_on_two_textured_anchors(self):
        image = map_frame('a', 'b')
        _, report = motion.settle(lambda: image, Mock(), Mock(), clock=lambda: 0,
                                  rois=[LABEL_A, LABEL_B], kind='map')
        self.assertTrue(report['confirmed'])
        self.assertEqual(report['frames'], 3)
        self.assertEqual(report['blank_streak'], 0)

    def test_blank_anchor_fails_fast_instead_of_burning_the_deadline(self):
        image = map_frame('a')            # LABEL_B is blank for the whole window
        clock = Mock(side_effect=[0, 0, 0, 0, 0, 0, 0, 0])
        _, report = motion.settle(lambda: image, Mock(), Mock(), clock=clock,
                                  rois=[LABEL_A, LABEL_B], kind='map')
        self.assertFalse(report['confirmed'])
        self.assertGreaterEqual(report['blank_streak'], 2)
        self.assertIn('reason', report)
        self.assertLess(report['frames'], 6)          # not the 3.5 s window
        self.assertLess(clock.call_count, 8)

    def test_fixed_mode_is_unaffected_by_the_anchor_gate(self):
        image = map_frame('a')
        _, report = motion.settle(lambda: image, Mock(), Mock(), clock=lambda: 0)
        self.assertTrue(report['confirmed'])
        self.assertEqual(report['blank_streak'], 0)


class ObserveAnchorSelectionTests(unittest.TestCase):
    CORNERS = ((200, 300, 280, 340), (700, 500, 780, 540), (1000, 200, 1080, 240))

    def setUp(self):
        # The fallback path logs; keep the live activity.log out of the tests.
        target = patch('autostory.navigation.log')
        target.start()
        self.addCleanup(target.stop)

    def labels(self):
        # OCR boxes are rectangle corners, unlike the (x,y,w,h) paint helpers above.
        return [{'text': code, 'score': .96, 'box': list(corner)}
                for code, corner in zip(('1-3', '1-7', '1-9'), self.CORNERS)]

    def build(self, frames, labels):
        from autostory.navigation import Navigator
        nav = Navigator.__new__(Navigator)
        nav.d = Mock()
        nav.d._needs_settle = True
        nav.d._settle_rois = ()
        nav.v = Mock()
        nav.d.shot.side_effect = list(frames)
        nav.v.texts.return_value = labels
        nav.d.stable_shot.return_value = frames[0]
        return nav

    def test_blank_label_box_is_dropped_before_the_map_gate(self):
        from autostory.home_observation import navigation_regions
        frame = map_frame('a', 'c')                  # middle box carries no label
        labels = self.labels()
        page, regions = navigation_regions(labels)
        self.assertEqual(page, 'map')
        self.assertEqual(len(regions), 3)
        expected = motion.textured_regions(frame, regions)
        self.assertEqual(len(expected), 2)
        nav = self.build([frame, frame], labels)
        nav.observe()
        nav.d.invalidate_frame.assert_called_once_with(expected, kind='map')

    def test_a_single_usable_anchor_cannot_claim_the_map_gate(self):
        frame = map_frame('a')                       # one box has label pixels
        nav = self.build([frame, frame, frame], self.labels())
        nav.observe()
        nav.d.invalidate_frame.assert_not_called()   # falls back to a whole-frame settle
        nav.d.stable_shot.assert_called_once()

    def test_no_usable_anchor_falls_back_to_a_whole_frame_settle(self):
        frame = map_frame()
        nav = self.build([frame, frame, frame], self.labels())
        nav.observe()
        nav.d.invalidate_frame.assert_not_called()
        nav.d.stable_shot.assert_called_once()


class TransientClassificationTests(unittest.TestCase):
    def test_capture_stream_loss_is_transient(self):
        for text in ('模拟器操作失败，已停止本轮操作：error: closed',
                     '模拟器操作失败，已停止本轮操作：error: device offline',
                     '模拟器操作失败，已停止本轮操作：error: no devices/emulators found',
                     '截图恢复时模拟器未就绪，进度已保存：offline',
                     '模拟器连接或响应异常，已停止本轮操作：timed out'):
            self.assertTrue(is_transient_device_error(DeviceUnavailableError(text)), text)

    def test_configuration_and_authorisation_faults_are_final(self):
        for text in ('指定模拟器 127.0.0.1:16384 不在线；在线设备：[]。请核对 device，未切换到其他设备',
                     '模拟器操作失败，已停止本轮操作：error: device unauthorized',
                     '无法唯一确定模拟器，在线设备：[emulator-5554]'):
            self.assertFalse(is_transient_device_error(DeviceUnavailableError(text)), text)

    def test_other_errors_are_never_classified_as_device_recovery(self):
        self.assertFalse(is_transient_device_error(RuntimeError('error: closed')))
        self.assertFalse(is_transient_device_error(InterruptedError('已停止；进度已保存')))


class DeviceProbeTests(unittest.TestCase):
    def device(self, result):
        device = Device.__new__(Device)
        device.adb = 'adb'
        device.serial = '127.0.0.1:16384'
        device.raw = Mock(return_value=result) if not isinstance(result, BaseException) \
            else Mock(side_effect=result)
        return device

    def test_ready_accepts_only_an_online_transport(self):
        self.assertTrue(self.device(b'device\n').ready())
        self.assertFalse(self.device(b'offline\n').ready())

    def test_ready_swallows_device_errors_but_never_a_stop(self):
        self.assertFalse(self.device(DeviceUnavailableError('模拟器操作失败，已停止本轮操作：error: closed')).ready())
        with self.assertRaises(InterruptedError):
            self.device(InterruptedError('已停止；进度已保存')).ready()


def book():
    return {'profiles': {'A': {}, 'B': {}}, 'stages': {
        'A-1:normal': {'group': 'A', 'code': 'A-1', 'mode': 'normal'},
        'B-1:normal': {'group': 'B', 'code': 'B-1', 'mode': 'normal'}}}


def record(code, state='uncleared'):
    return {'code': code, 'mode': 'normal', 'state': state,
            'at': '2026-11-01T00:00:00+08:00', 'evidence': 'screen.png'}


TRANSIENT = '模拟器操作失败，已停止本轮操作：error: closed'
FINAL = '指定模拟器 127.0.0.1:16384 不在线；在线设备：[]。请核对 device，未切换到其他设备'


class ScanRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA', self.root), ('catalogue', Mock(return_value=book())),
                            ('check_stop', Mock()), ('log', Mock()), ('export_coverage', Mock()),
                            ('settings', Mock(return_value={}))]:
            target = patch.object(scanning, name, value)
            target.start()
            self.addCleanup(target.stop)
        target = patch.object(scanning.workflow, 'update')
        target.start()
        self.addCleanup(target.stop)
        self.nav = Mock()
        self.nav.on_scan_stage = None
        write_json(self.root / 'campaign-results.json',
                   {'sanity': 107, 'count': 5, 'practice_count': 18})

    def set_result(self, group):
        db = read_json(self.root / 'progress.json', {})
        row = record(group + '-1')
        db[group + '-1:normal'] = row
        write_json(self.root / 'progress.json', db)
        self.nav.on_scan_stage(row)

    def test_transient_loss_is_waited_out_and_the_same_group_resumes(self):
        self.nav.enter.side_effect = [DeviceUnavailableError(TRANSIENT), None, None]
        self.nav.scan.side_effect = lambda group, **kw: self.set_result(group)
        result = scanning.scan_all(self.nav)
        self.assertEqual(result['state'], 'done')
        self.assertEqual(self.nav.enter.call_count, 3)      # A retried once, then B
        self.assertEqual(self.nav.enter.call_args_list[0], unittest.mock.call('A'))
        self.assertEqual(self.nav.enter.call_args_list[1], unittest.mock.call('A'))
        self.nav.home.assert_called_once()                  # verified page before retry
        saved = read_json(self.root / 'scan-report.json')
        self.assertEqual(saved['summary']['confirmed'], 2)
        self.assertTrue(any('自动恢复' in str(call) for call in scanning.log.call_args_list))

    def test_configuration_fault_still_ends_the_run_without_retry(self):
        self.nav.enter.side_effect = DeviceUnavailableError(FINAL)
        with self.assertRaises(DeviceUnavailableError):
            scanning.scan_all(self.nav)
        self.assertEqual(self.nav.enter.call_count, 1)
        self.assertEqual(read_json(self.root / 'scan-report.json')['state'], 'error')

    def test_user_stop_is_never_retried(self):
        self.nav.enter.side_effect = InterruptedError('已停止；进度已保存')
        with self.assertRaises(InterruptedError):
            scanning.scan_all(self.nav)
        self.assertEqual(self.nav.enter.call_count, 1)
        self.assertEqual(read_json(self.root / 'scan-report.json')['state'], 'stopped')
        self.nav.home.assert_not_called()

    def test_emulator_that_stays_down_ends_the_run_after_one_wait(self):
        self.nav.enter.side_effect = DeviceUnavailableError(TRANSIENT)
        self.nav.d.ready.return_value = False
        with patch.object(scanning, 'RECOVERY_POLL', 0), \
             patch.object(scanning, 'time') as clock, \
             patch.object(scanning, 'RECOVERY_WAITS', (0.,)), \
             patch.object(scanning, '_sleep'):
            clock.monotonic.return_value = 0
            with self.assertRaises(DeviceUnavailableError):
                scanning.scan_all(self.nav)
        self.assertEqual(self.nav.enter.call_count, 1)
        self.assertEqual(read_json(self.root / 'scan-report.json')['state'], 'error')

    def test_recovery_budget_is_finite(self):
        self.nav.enter.side_effect = DeviceUnavailableError(TRANSIENT)
        with patch.object(scanning, 'recovery_plan', return_value=(True, 2, [0., 0.])), \
             patch.object(scanning, 'wait_for_device', return_value=True):
            with self.assertRaises(DeviceUnavailableError):
                scanning.scan_all(self.nav)
        self.assertEqual(self.nav.enter.call_count, 3)      # first try + two waits
        saved = read_json(self.root / 'scan-report.json')
        self.assertEqual(saved['state'], 'error')
        self.assertTrue(any('超过自动恢复上限' in str(call) for call in scanning.log.call_args_list))

    def test_recovery_can_be_disabled_from_settings(self):
        with patch.object(scanning, 'settings',
                          Mock(return_value={'device_recovery': {'enabled': False}})):
            enabled, attempts, waits = scanning.recovery_plan()
        self.assertFalse(enabled)
        self.assertEqual(attempts, scanning.RECOVERY_ATTEMPTS)
        with patch.object(scanning, 'recovery_plan', return_value=(False, 3, [0.])):
            self.nav.enter.side_effect = DeviceUnavailableError(TRANSIENT)
            with self.assertRaises(DeviceUnavailableError):
                scanning.scan_all(self.nav)
        self.assertEqual(self.nav.enter.call_count, 1)

    def test_recovery_plan_reads_attempt_and_wait_overrides(self):
        self.assertTrue(scanning.recovery_plan()[0])
        with patch.object(scanning, 'settings',
                          Mock(return_value={'device_recovery': {'attempts': 1, 'waits': [3]}})):
            enabled, attempts, waits = scanning.recovery_plan()
        self.assertTrue(enabled)
        self.assertEqual(attempts, 1)
        self.assertEqual(waits, [3.0])


class ScanResumeButtonTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / 'web/index.html').read_text('utf-8')

    def test_notice_exposes_an_optional_action_button(self):
        self.assertIn('id="noticeAction"', self.html)
        self.assertIn('.notice-action{', self.html)
        self.assertIn('noticeAction: $(\'noticeAction\')', self.html)
        self.assertIn("els.noticeAction.addEventListener('click', runNoticeAction);", self.html)

    def test_resume_shortcut_is_limited_to_failed_read_only_scans(self):
        self.assertIn('function scanRecoveryAction(status)', self.html)
        self.assertIn("['scan-all', 'scan', 'scan-current'].indexOf(action) < 0", self.html)
        self.assertIn('return { label: \'继续扫描\', run: function () { submitAction(\'scan-all\'); } };', self.html)
        self.assertIn("dispatchNotice('error', statusDetail(status), true, scanRecoveryAction(status));", self.html)

    def test_other_notices_clear_the_action(self):
        self.assertIn('function setNoticeAction(action)', self.html)
        self.assertIn('noticeActionHandler = null;', self.html)


if __name__ == '__main__':
    unittest.main()
