import unittest
from unittest.mock import Mock, patch
import numpy as np
from autostory.motion import signature, frame_motion, settle
from autostory.device import Device


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.image = np.random.default_rng(42).integers(0,256,(720,1280,3),dtype=np.uint8)
        # Large UI-like features survive downsampling.
        self.image[100:400,100:400] = 240
        self.image[400:600,300:800] = 10

    def test_identical_textured_frames_are_stable(self):
        sig=signature(self.image)
        self.assertTrue(frame_motion(sig,sig)['stable'])

    def test_loading_black_is_never_stable(self):
        sig=signature(np.zeros_like(self.image))
        self.assertFalse(frame_motion(sig,sig)['stable'])

    def test_scroll_is_unstable(self):
        self.assertFalse(frame_motion(signature(self.image),signature(np.roll(self.image,60,axis=1)))['stable'])

    def test_transition_must_be_followed_by_two_stable_comparisons(self):
        frames=[np.zeros_like(self.image),self.image,self.image,self.image]
        capture=Mock(side_effect=frames)
        image, report=settle(capture,Mock(),Mock(),clock=lambda:0)
        self.assertTrue(report['confirmed']); self.assertEqual(report['frames'],4)
        self.assertIs(image,self.image)

    def test_timeout_returns_unconfirmed_and_does_not_click(self):
        image, report=settle(lambda:np.zeros_like(self.image),Mock(),Mock(),clock=Mock(side_effect=[0,0,4]))
        self.assertFalse(report['confirmed']);self.assertEqual(report['frames'],2)

    def test_stop_and_device_failure_propagate(self):
        for error in (InterruptedError('stop'),ConnectionError('offline')):
            with self.assertRaises(type(error)):
                settle(Mock(side_effect=error),Mock(),Mock())

    def test_device_never_reuses_old_pixels_after_settle(self):
        device=Device.__new__(Device);device._needs_settle=False
        device.shot=Mock(return_value=self.image)
        self.assertIs(device.stable_shot(),self.image);device.shot.assert_called_once()

    def test_stable_but_late_frame_is_not_confirmed(self):
        _,report=settle(lambda:self.image,Mock(),Mock(),clock=Mock(side_effect=[0,0,1,4]))
        self.assertEqual(report['frames'],3)
        self.assertFalse(report['confirmed'])

    def test_stop_during_capture_discards_returned_frame(self):
        stop=Mock(side_effect=[None,InterruptedError('stop')])
        with self.assertRaises(InterruptedError):
            settle(lambda:self.image,stop,Mock())

    def test_successful_settle_consumes_pending_flag(self):
        device=Device.__new__(Device);device._capture=Mock(return_value=self.image)
        with patch('autostory.device.check_stop'),patch('autostory.device.STOP_WAIT'):
            self.assertIs(device.stable_shot(),self.image)
        self.assertFalse(device._needs_settle);self.assertEqual(device._capture.call_count,3)

if __name__=='__main__':unittest.main()
