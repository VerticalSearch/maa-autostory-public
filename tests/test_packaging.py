import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
import threading
import desktop
from autostory.common import worker_command

class PackagingTests(unittest.TestCase):
    def test_fallback_window_loop_starts_while_service_is_still_loading(self):
        started=threading.Event();release=threading.Event();ended=threading.Event()
        def slow_service(*args):
            started.set()
            try:release.wait(2)
            finally:ended.set()
        app=Mock()
        def run():
            self.assertTrue(started.wait(1))
            self.assertFalse(ended.is_set())
            release.set()
        app.run.side_effect=run
        with patch('desktop.launch_web_desktop',return_value=False), \
             patch('desktop_ui.AutoStoryDesktop',return_value=app),patch('desktop.ensure_service',side_effect=slow_service):
            try:desktop.launch_desktop(Path('.'),'http://127.0.0.1:1')
            finally:release.set();ended.wait(2)
        app.run.assert_called_once()

    def test_frozen_worker_uses_executable_dispatch(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',r'C:\App\helper.exe'):
            self.assertEqual(worker_command('plan','--chapter','CW-EX'),[r'C:\App\helper.exe','--worker','plan','--chapter','CW-EX'])

    def test_source_worker_preserves_cli_contract(self):
        with patch.object(sys,'frozen',False,create=True):
            command=worker_command('diagnose')
            self.assertEqual(command[1],'-u')
            self.assertTrue(command[2].endswith('worker.py'))
            self.assertEqual(command[3],'diagnose')

    @unittest.skipUnless(sys.platform=='win32','Windows mutex')
    def test_duplicate_launch_has_one_window_owner(self):
        with tempfile.TemporaryDirectory() as d:
            first=desktop.WindowInstance(Path(d));second=desktop.WindowInstance(Path(d))
            try:
                self.assertTrue(first.owned);self.assertFalse(second.owned)
            finally:second.close();first.close()
            third=desktop.WindowInstance(Path(d))
            try:self.assertTrue(third.owned)
            finally:third.close()

    def test_ready_service_is_reused_without_spawning(self):
        with patch('desktop.health',return_value={'pid':42}) as h,patch('desktop.subprocess.Popen') as spawn:
            self.assertEqual(desktop.ensure_service(Path('.'),'http://127.0.0.1:1'),{'pid':42})
        spawn.assert_not_called()

    def test_other_installation_is_rejected(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock()
        response.read.return_value=b'{"application":"MAA-AutoStory","root":"C:/somewhere/else"}'
        with patch('desktop.local_urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'另一套程序'):desktop.health('http://127.0.0.1:1',Path('C:/expected'))

if __name__=='__main__':unittest.main()
