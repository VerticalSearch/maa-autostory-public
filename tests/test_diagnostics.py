import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from autostory import diagnostics as d

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'runtime';self.data.mkdir()
        for path in ('MaaCore.dll','resource/tasks.json','resource/battle_data.json','resource/Arknights-Tile-Pos/overview.json'):
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
        for name,value in [('DATA',self.data),('MAA',self.root)]:
            item=patch.object(d,name,value);item.start();self.addCleanup(item.stop)
        for name in ('check_stop','log'):
            item=patch.object(d,name);item.start();self.addCleanup(item.stop)
        self.device=Mock(serial='emulator-test')
        self.image=np.zeros((720,1280,3),np.uint8);self.image[:,640:]=255
        self.device.raw.return_value=cv2.imencode('.png',self.image)[1].tobytes()
        self.factory=Mock(return_value=self.device)
    def report(self):return json.loads((self.data/'diagnostics.json').read_text('utf-8'))
    def test_single_bounded_capture_and_no_input_or_core(self):
        result=d.run_diagnostics(self.factory)
        self.assertEqual(result['state'],'ok');self.factory.assert_called_once_with(connect_timeout=5)
        self.device.raw.assert_called_once_with('-s','emulator-test','exec-out','screencap','-p',timeout=8)
        self.device.tap.assert_not_called();self.device.back.assert_not_called();self.device.swipe.assert_not_called()
        self.assertTrue((self.data/'diagnose.png').is_file())
    def test_blank_frame_is_warning_not_battle_ready(self):
        self.device.raw.return_value=cv2.imencode('.png',np.zeros_like(self.image))[1].tobytes()
        self.assertEqual(d.run_diagnostics(self.factory)['state'],'warning')
    def test_new_maa_nested_tasks_layout_is_supported(self):
        (self.root/'resource/tasks.json').unlink()
        p=self.root/'resource/tasks/tasks.json';p.parent.mkdir();p.write_text('{}')
        self.assertEqual(d.run_diagnostics(self.factory)['state'],'ok')
    def test_invalid_capture_has_persisted_actionable_failure(self):
        self.device.raw.return_value=b'bad'
        with self.assertRaisesRegex(RuntimeError,'截图'):d.run_diagnostics(self.factory)
        self.assertEqual(self.report()['state'],'error');self.assertIn('重新连接',self.report()['advice'])
    def test_device_error_is_reported_before_capture(self):
        self.factory.side_effect=RuntimeError('尚未配置 ADB')
        with self.assertRaisesRegex(RuntimeError,'adb_path'):d.run_diagnostics(self.factory)
        self.device.raw.assert_not_called();self.assertEqual(self.report()['state'],'error')
    def test_resource_missing_not_silently_ok(self):
        (self.root/'MaaCore.dll').unlink()
        with self.assertRaisesRegex(RuntimeError,'资源'):d.run_diagnostics(self.factory)
        self.assertEqual(self.report()['state'],'error')
    def test_wrong_aspect_ratio_requires_configuration_fix(self):
        self.device.raw.return_value=cv2.imencode('.png',np.zeros((800,800,3),np.uint8))[1].tobytes()
        with self.assertRaisesRegex(RuntimeError,'16:9'):d.run_diagnostics(self.factory)
        self.assertIn('分辨率',self.report()['advice'])
    def test_stop_does_not_capture(self):
        d.check_stop.side_effect=InterruptedError('stop')
        with self.assertRaises(InterruptedError):d.run_diagnostics(self.factory)
        self.device.raw.assert_not_called();self.assertEqual(self.report()['state'],'stopped')

if __name__=='__main__':unittest.main()
