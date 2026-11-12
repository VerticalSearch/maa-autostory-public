import unittest
from unittest.mock import patch
from autostory.device import Device
from autostory.common import DeviceUnavailableError


class DeviceConfigurationTests(unittest.TestCase):
    def test_explicit_offline_target_never_selects_another_device(self):
        for target in ('wanted-device','127.0.0.1:1111'):
            with self.subTest(target=target),patch('autostory.device.settings',return_value={'adb_path':'fake-adb','device':target}), \
                 patch('autostory.device.Path.exists',return_value=True), \
                 patch.object(Device,'raw',return_value=b'other-device\tdevice\n') as raw:
                with self.assertRaisesRegex(DeviceUnavailableError,'未切换到其他设备'):Device()
                self.assertTrue(all('screencap' not in c.args and 'input' not in c.args for c in raw.call_args_list))
    def test_explicit_settings_do_not_require_or_read_maa_gui_config(self):
        with patch('autostory.device.settings',return_value={'adb_path':'fake-adb','device':'emulator-5554'}), \
             patch('autostory.device.read_json',side_effect=AssertionError('must not read GUI config')), \
             patch('autostory.device.Path.exists',return_value=True), \
             patch.object(Device,'raw',return_value=b'emulator-5554\tdevice\n'):
            d=Device()
        self.assertEqual((d.adb,d.serial),('fake-adb','emulator-5554'))

    def test_missing_or_incomplete_gui_config_has_actionable_error(self):
        for cfg in ({},None,{'Current':'gone','Configurations':{}},{'Configurations':[]}, {'Current':'x','Configurations':{'x':{'Gui':None}}}):
            with self.subTest(cfg=cfg),patch('autostory.device.settings',return_value={}), \
                 patch('autostory.device.read_json',return_value=cfg),patch.object(Device,'raw') as raw:
                with self.assertRaisesRegex(RuntimeError,'adb_path'):Device()
                raw.assert_not_called()

    def test_explicit_adb_and_single_device_without_gui_config(self):
        with patch('autostory.device.settings',return_value={'adb_path':'fake-adb'}), \
             patch('autostory.device.read_json',return_value={}), \
             patch('autostory.device.Path.exists',return_value=True), \
             patch.object(Device,'raw',return_value=b'emulator-5554\tdevice\n'):
            self.assertEqual(Device().serial,'emulator-5554')

    def test_explicit_adb_never_guesses_between_two_online_devices(self):
        with patch('autostory.device.settings',return_value={'adb_path':'fake-adb'}), \
             patch('autostory.device.read_json',return_value={}), \
             patch('autostory.device.Path.exists',return_value=True), \
             patch.object(Device,'raw',return_value=b'emulator-5554\tdevice\nemulator-5556\tdevice\n'):
            with self.assertRaisesRegex(RuntimeError,'无法唯一确定'):Device()

    def test_explicit_serial_overrides_maa_address_and_uses_maa_adb(self):
        cfg={'Current':'c','Configurations':{'c':{'Gui':{'ConnectSettings':{'AdbPath':'maa-adb','Address':'old-device'}}}}}
        with patch('autostory.device.settings',return_value={'device':'new-device'}), \
             patch('autostory.device.read_json',return_value=cfg), \
             patch('autostory.device.Path.exists',return_value=True), \
             patch.object(Device,'raw',return_value=b'new-device\tdevice\nold-device\tdevice\n'):
            d=Device()
        self.assertEqual((d.adb,d.serial),('maa-adb','new-device'))

if __name__=='__main__':unittest.main()
