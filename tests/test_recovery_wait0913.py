import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autostory.recovery import recover_practice,RecoveryFailure
from autostory.motion import UnsettledFrameError

class RecoveryWaitTests(unittest.TestCase):
    def setUp(self):
        stop=patch('autostory.recovery.check_stop');self.stop=stop.start();self.addCleanup(stop.stop)
    def nav(self,frames):
        nav=Mock();nav.observe.side_effect=[(None,t) if isinstance(t,list) else t for t in frames]
        nav.visible_stages.side_effect=lambda texts,prefix:[1] if any(t['text']=='M8-6' for t in texts) else []
        nav.find.return_value={'code':'M8-6','mode':'raid','state':'uncleared'}
        return nav
    def test_black_transition_longer_than_eight_frames_recovers_without_extra_clicks(self):
        nav=self.nav([[{'text':'任务失败'},{'text':'点击画面以继续'}]]+[[]]*12+[[{'text':'M8-6'}]])
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.recovery.DATA',Path(tmp)),patch('autostory.recovery.time.sleep'):
            result=recover_practice(nav,'M8-6','raid','8')
        self.assertEqual(result['result'],'recovered');self.assertEqual(nav.observe.call_count,14)
        nav.click_text.assert_called_once();nav.d.tap.assert_not_called();nav.d.back.assert_not_called()
    def test_continuous_black_frames_stop_at_time_limit_without_input(self):
        nav=self.nav([[]]*48);elapsed=[0.0]
        def wait(seconds):elapsed[0]+=seconds
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.recovery.DATA',Path(tmp)), \
             patch('autostory.recovery.time.monotonic',side_effect=lambda:elapsed[0]),patch('autostory.recovery.time.sleep',side_effect=wait):
            with self.assertRaises(RecoveryFailure):recover_practice(nav,'M8-6','raid','8',timeout=3)
        self.assertEqual(elapsed[0],3);self.assertEqual(nav.observe.call_count,6)
        nav.click_text.assert_not_called();nav.d.tap.assert_not_called();nav.d.back.assert_not_called()
    def test_loading_settle_window_can_retry_observation_without_input(self):
        nav=self.nav([UnsettledFrameError('loading'),[{'text':'M8-6'}]])
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.recovery.DATA',Path(tmp)),patch('autostory.recovery.time.sleep'):
            self.assertEqual(recover_practice(nav,'M8-6','raid','8')['result'],'recovered')
        self.assertEqual(nav.observe.call_count,2);nav.click_text.assert_not_called();nav.d.tap.assert_not_called()
    def test_user_stop_during_loading_never_captures_or_clicks(self):
        nav=self.nav([[]]);self.stop.side_effect=InterruptedError('stop')
        with tempfile.TemporaryDirectory() as tmp,patch('autostory.recovery.DATA',Path(tmp)):
            with self.assertRaises(InterruptedError):recover_practice(nav,'M8-6','raid','8')
        nav.observe.assert_not_called();nav.d.tap.assert_not_called()

if __name__=='__main__':unittest.main()
