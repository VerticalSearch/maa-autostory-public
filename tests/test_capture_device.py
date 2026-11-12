import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from autostory.device import Device, STOP_WAIT
from autostory.common import DeviceUnavailableError


class CaptureDeviceTests(unittest.TestCase):
    def setUp(self):
        self.device=Device.__new__(Device)
        self.device.serial='emulator-5554'
        self.device.size=(1280,720)
        self.device._capture_transport='raw'
        self.image=np.zeros((720,1280,3),np.uint8)
        self.image[50:550,100:1000]=[11,127,243]
        self.png=cv2.imencode('.png',self.image)[1].tobytes()
        self.raw=struct.pack('<4I',1280,720,1,1)+cv2.cvtColor(self.image,cv2.COLOR_BGR2RGBA).tobytes()
        self.stop_patch=patch('autostory.device.check_stop')
        self.stop_patch.start();self.addCleanup(self.stop_patch.stop)

    def test_valid_raw_color_matches_png_without_retry(self):
        self.device.raw=Mock(return_value=self.raw)
        actual=self.device._capture()
        np.testing.assert_array_equal(actual,self.image)
        self.assertEqual(self.device.last_capture['transport'],'raw')
        self.assertEqual(self.device.raw.call_count,1)
        self.assertNotIn('-p',self.device.raw.call_args.args)

    def test_truncated_modern_frame_falls_back_once_and_stays_png(self):
        self.device.raw=Mock(side_effect=[self.raw[:-4],self.png,self.png])
        with patch('autostory.device.log'):
            np.testing.assert_array_equal(self.device._capture(),self.image)
            np.testing.assert_array_equal(self.device._capture(),self.image)
        self.assertEqual(self.device._capture_transport,'png')
        self.assertTrue(self.device.last_capture['fallback'])
        self.assertEqual([c.args[-1] for c in self.device.raw.call_args_list],['screencap','-p','-p'])

    def test_unsupported_colorspace_falls_back(self):
        raw=self.raw[:12]+struct.pack('<I',2)+self.raw[16:]
        self.device.raw=Mock(side_effect=[raw,self.png])
        with patch('autostory.device.log'):
            np.testing.assert_array_equal(self.device._capture(),self.image)
        self.assertEqual(self.device.last_capture['transport'],'png')

    def test_connection_error_and_stop_never_trigger_capture_retry(self):
        for error in (DeviceUnavailableError('offline'),InterruptedError('stop')):
            self.device.raw=Mock(side_effect=error)
            with self.assertRaises(type(error)):self.device._capture()
            self.device.raw.assert_called_once()

    def test_both_invalid_formats_stop(self):
        self.device.raw=Mock(side_effect=[b'bad',b'bad'])
        with patch('autostory.device.log'),self.assertRaises(DeviceUnavailableError):self.device._capture()

    def test_empty_png_is_device_failure(self):
        self.device._capture_transport='png';self.device.raw=Mock(return_value=b'')
        with self.assertRaises(DeviceUnavailableError):self.device._capture()

    def test_auto_uses_raw_only_on_local_emulator_connections(self):
        for serial,expected in (('emulator-5554','raw'),('127.0.0.1:7555','raw'),
                                ('192.168.1.20:5555','png'),('USBDEVICE','png')):
            cfg={'Current':'c','Configurations':{'c':{'Gui':{'ConnectSettings':{
                'AdbPath':'fake-adb','Address':serial}}}}}
            with patch('autostory.device.read_json',return_value=cfg), \
                 patch('autostory.device.settings',return_value={}), \
                 patch('autostory.device.Path.exists',return_value=True), \
                 patch.object(Device,'raw',return_value=f'{serial}\tdevice\n'.encode()):
                device=Device()
            self.assertEqual(device._capture_transport,expected)

    def test_scaled_frame_keeps_native_input_coordinates(self):
        self.device._capture_transport='png'
        large=cv2.resize(self.image,(1920,1080))
        self.device.raw=Mock(return_value=cv2.imencode('.png',large)[1].tobytes())
        self.assertEqual(self.device._capture().shape,(720,1280,3))
        with patch('autostory.device.assert_gui_idle'):
            self.device.tap(640,360,wait=0)
        self.assertEqual(self.device.raw.call_args.args[-2:],(960,540))

    def test_interrupted_input_invalidates_prior_stability(self):
        for action,args in (('tap',(20,30)),('swipe',(10,20,30,40)),('back',())):
            self.device._needs_settle=False
            self.device.raw=Mock(side_effect=InterruptedError('unknown input result'))
            with patch('autostory.device.assert_gui_idle'),self.assertRaises(InterruptedError):
                getattr(self.device,action)(*args)
            self.assertTrue(self.device._needs_settle)
            self.assertEqual(self.device.raw.call_count,1)

    def test_stop_file_interrupts_wait_without_inprocess_event(self):
        # This is the cross-process stop path used by the control service.
        from autostory import common
        self.stop_patch.stop()
        with tempfile.TemporaryDirectory() as temp,patch.object(common,'DATA',Path(temp)), \
             patch.object(common,'STOP',threading.Event()),patch.object(common,'_STOP_THRESHOLD_NS',None):
            timer=threading.Timer(.12,lambda:(Path(temp)/'STOP').write_text('stop'))
            timer.start();started=time.monotonic()
            try:
                with self.assertRaises(InterruptedError):STOP_WAIT(5)
                self.assertLess(time.monotonic()-started,.8)
                self.assertFalse(common.STOP.is_set())
            finally:timer.join()

if __name__=='__main__':unittest.main()
