"""Offline supervision: input ownership, liveness, stop and checkpoint scope."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autostory import startup
from autostory.core import Core


def screen(state, progress=None, button=None, foreground=startup.GAME):
    return dict(state=state,reason=state,progress=progress,button=button,foreground=foreground,texts=[])


class StartupSupervisionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.clock=0.
        def pause(seconds):self.clock+=seconds
        for target,value in (('DATA',self.root),('check_stop',Mock()),('log',Mock()),
                             ('settings',lambda:{}),('_pause',pause)):
            p=patch.object(startup,target,value);p.start();self.addCleanup(p.stop)
        p=patch.object(startup.time,'monotonic',lambda:self.clock);p.start();self.addCleanup(p.stop)
        self.device=Mock(serial='selected-device');self.vision=Mock();self.core=Mock()
        p=patch.object(startup,'_read');self.read=p.start();self.addCleanup(p.stop)

    def frames(self,*states):
        items=list(states)
        self.read.side_effect=lambda *args:(None,items.pop(0) if len(items)>1 else items[0])

    def test_ready_needs_two_frames_and_no_input(self):
        self.frames(screen('ready'))
        result=startup.ensure_ready(self.device,self.vision,self.core)
        self.assertEqual(result['state'],'ready');self.assertEqual(self.read.call_count,2)
        self.core.run.assert_not_called();self.device.tap.assert_not_called()

    def test_installer_is_reobserved_then_loading_then_ready(self):
        button=dict(text='更新',score=.99,x=921,y=477)
        self.frames(screen('install_confirm',button=button),screen('install_confirm',button=button),
                    screen('installing'),screen('loading'),screen('ready'),screen('ready'))
        result=startup.ensure_ready(self.device,self.vision,self.core)
        self.device.tap.assert_called_once_with(921,477,.5)
        self.assertEqual(result['actions'],1);self.core.run.assert_not_called()

    def test_changed_dialog_does_not_click_old_button(self):
        button=dict(text='更新',score=.99,x=921,y=477)
        self.frames(screen('install_confirm',button=button),screen('ready'),screen('ready'),screen('ready'))
        startup.ensure_ready(self.device,self.vision,self.core)
        self.device.tap.assert_not_called()

    def test_paused_battle_and_login_need_attention_without_input(self):
        for state in ('battle','attention'):
            self.frames(screen(state))
            with self.assertRaises(startup.StartupError):startup.ensure_ready(self.device,self.vision,self.core)
        self.device.tap.assert_not_called();self.core.run.assert_not_called()

    def test_unknown_and_stalled_loading_are_bounded_and_saved(self):
        for state in ('unknown','loading'):
            self.clock=0;self.frames(screen(state))
            with self.assertRaisesRegex(startup.StartupError,'持续未取得'):
                startup.ensure_ready(self.device,self.vision,self.core)
        self.assertTrue(list((self.root/'screens/startup').glob('*.json')))
        self.device.tap.assert_not_called()

    def test_same_dialog_clicks_once_then_times_out(self):
        button=dict(text='更新',score=.99,x=921,y=477)
        self.frames(screen('install_confirm',button=button))
        with self.assertRaises(startup.StartupError):startup.ensure_ready(self.device,self.vision,self.core)
        self.device.tap.assert_called_once()

    def test_scan_current_never_launches_or_updates(self):
        self.frames(screen('launcher'))
        with self.assertRaises(startup.StartupError):
            startup.ensure_ready(self.device,self.vision,self.core,allow_launch=False)
        self.device.tap.assert_not_called();self.core.run.assert_not_called()

    def test_settlement_only_hands_off_to_existing_campaign_recovery(self):
        self.frames(screen('settlement'))
        with self.assertRaises(startup.StartupError):
            startup.ensure_ready(self.device,self.vision,self.core)
        result=startup.ensure_ready(self.device,self.vision,self.core,allow_settlement=True)
        self.assertEqual(result['state'],'settlement')
        self.device.tap.assert_not_called();self.core.run.assert_not_called()

    def test_stop_propagates_without_further_input(self):
        self.frames(screen('installing'))
        with patch.object(startup,'check_stop',side_effect=InterruptedError('stop')):
            with self.assertRaises(InterruptedError):startup.ensure_ready(self.device,self.vision,self.core)
        self.device.tap.assert_not_called();self.core.run.assert_not_called()

    def test_installer_handling_does_not_delegate_unguarded_startup(self):
        button=dict(text='更新',score=.99,x=921,y=477)
        self.frames(screen('login_start'),screen('install_confirm',button=button),
                    screen('install_confirm',button=button),screen('install_confirm',button=button),
                    screen('ready'),screen('ready'))
        startup.ensure_ready(self.device,self.vision,self.core)
        self.core.run.assert_not_called();self.device.tap.assert_called_once()

    def test_two_different_startup_pages_can_click_their_own_verified_button(self):
        resource=dict(text='资源页开始',score=.99,x=640,y=681)
        wake=dict(text='开始唤醒',score=.99,x=640,y=510)
        self.frames(screen('login_start',button=resource),screen('login_start',button=resource),
                    screen('login_start',button=wake),screen('login_start',button=wake),screen('ready'))
        result=startup.ensure_ready(self.device,self.vision,self.core)
        self.assertEqual(result['actions'],2)
        self.assertEqual([call.args[:2] for call in self.device.tap.call_args_list],[(640,681),(640,510)])
        self.core.run.assert_not_called()

    def test_launcher_starts_only_selected_game_once(self):
        self.frames(screen('launcher'),screen('launcher'),screen('loading'),screen('ready'))
        result=startup.ensure_ready(self.device,self.vision,self.core)
        self.assertEqual(result['launches'],1)
        self.device.raw.assert_called_once_with('-s','selected-device','shell','monkey','-p',startup.GAME,'-c','android.intent.category.LAUNCHER','1')
        self.core.run.assert_not_called();self.device.tap.assert_not_called()

    def test_title_without_verified_button_never_falls_back_to_core(self):
        self.frames(screen('login_start'))
        with self.assertRaises(startup.StartupError):startup.ensure_ready(self.device,self.vision,self.core)
        self.core.run.assert_not_called();self.device.tap.assert_not_called()

    def test_numeric_progress_not_spinner_resets_stall(self):
        sup=startup._Supervisor(self.device,self.vision,True)
        self.frames(screen('download',10),screen('download',20),screen('download',20))
        sup.observe();self.clock=170;sup.observe();self.clock=340;sup.observe()
        self.clock=352
        with self.assertRaises(startup.StartupError):sup.observe()

    def test_total_timeout_wins_over_changing_states(self):
        sup=startup._Supervisor(self.device,self.vision,True)
        self.frames(screen('loading'),screen('download',30))
        sup.observe();self.clock=1201
        with self.assertRaisesRegex(startup.StartupError,'总等待'):sup.observe()

    def test_terminal_failure_on_ready_screen_does_not_restart(self):
        self.frames(screen('ready'))
        nav=Mock(d=self.device,v=self.vision,core=self.core)
        self.assertFalse(startup.recover_scan_startup(nav));self.core.run.assert_not_called()

    def test_limits_reject_invalid_nonfinite_and_extreme_values(self):
        with patch.object(startup,'settings',return_value={'startup':dict(total_timeout=float('nan'),stall_timeout=-1,unknown_timeout=99999)}):
            self.assertEqual(startup._limits(),dict(total_timeout=1200,stall_timeout=15,unknown_timeout=60))


class CorePollingTests(unittest.TestCase):
    def test_poll_failure_stops_running_core(self):
        core=Core.__new__(Core);core.events=[];core.asst=Mock();core.device=Mock()
        running=[True]
        core.asst.running.side_effect=lambda:running[0]
        core.asst.stop.side_effect=lambda:running.__setitem__(0,False)
        core.asst.start.side_effect=lambda:(running.__setitem__(0,True) or True)
        poll=Mock(side_effect=[None,RuntimeError('installer handoff')])
        with patch('autostory.core.check_stop'),patch('autostory.core.assert_gui_idle'):
            with self.assertRaisesRegex(RuntimeError,'installer handoff'):
                core.run('StartUp',{},poll_hook=poll)
        self.assertFalse(running[0]);self.assertEqual(core.asst.stop.call_count,2)


class ForegroundTests(unittest.TestCase):
    def test_mumu_multiple_stale_displays_use_only_current_focus(self):
        dump='''mCurrentFocus=null
        mFocusedApp=ActivityRecord{u0 app.lawnchair/.LawnchairLauncher}
        mCurrentFocus=null
        mFocusedApp=ActivityRecord{u0 com.android.packageinstaller/.InstallSuccess}
        mCurrentFocus=Window{u0 com.hypergryph.arknights/com.u8.sdk.U8UnityContext}'''
        self.assertEqual(startup._focused_package(dump),startup.GAME)

    def test_ambiguous_or_no_current_focus_never_uses_stale_activity(self):
        for dump in ('mCurrentFocus=null\nmFocusedApp=x com.hypergryph.arknights/.Game',
                     'mCurrentFocus=x a.b/.A\nmCurrentFocus=x c.d/.B',
                     'mCurrentFocus=x a.b/.A\nmCurrentFocus=x a.b/.A'):
            self.assertIsNone(startup._focused_package(dump))


class ScanStartupRecoveryTests(unittest.TestCase):
    def test_recovery_keeps_same_group_then_uses_existing_incremental_scan(self):
        from autostory import scanning
        from autostory.terminal_navigation import TerminalNavigationError
        import test_scan0922 as support
        case=support.ScanOnlyTests();case.setUp();self.addCleanup(case.doCleanups)
        case.nav.startup_recovery=True
        case.nav.enter.side_effect=[TerminalNavigationError('startup'),None,None]
        with patch.object(startup,'recover_scan_startup',return_value=True) as recover:
            scanning.scan_all(case.nav)
        calls=[c.args[0] for c in case.nav.enter.call_args_list]
        self.assertEqual(calls[:2],['A','A']);recover.assert_called_once_with(case.nav)
        self.assertEqual(scanning.read_json(case.root/'campaign-results.json'),case.budget)

    def test_repeated_shared_failure_is_not_restarted_forever(self):
        from autostory import scanning
        from autostory.terminal_navigation import TerminalNavigationError
        import test_scan0922 as support
        case=support.ScanOnlyTests();case.setUp();self.addCleanup(case.doCleanups)
        case.nav.startup_recovery=True
        case.nav.enter.side_effect=TerminalNavigationError('startup')
        with patch.object(startup,'recover_scan_startup',return_value=True) as recover:
            with self.assertRaises(TerminalNavigationError):scanning.scan_all(case.nav)
        self.assertEqual(case.nav.enter.call_count,2);recover.assert_called_once()
        self.assertEqual(scanning.read_json(case.root/'scan-report.json')['active_group'],'A')


if __name__=='__main__':unittest.main()
