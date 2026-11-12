import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from autostory import common, controller, scanning, terminal_navigation as terminal
from autostory.common import DeviceUnavailableError, read_json, write_json
import test_scan0922 as scan_support


def label(text, box):
    x1,y1,x2,y2 = box
    return dict(text=text, score=.99, box=box, x=(x1+x2)/2, y=(y1+y2)/2)


def home_labels():
    return [label('采购中心',[762,466,914,525]), label('公开招募',[943,490,1064,535]),
            label('编队',[700,310,784,363]), label('终端',[900,140,1020,220])]


def terminal_labels():
    return [label('终端',[70,650,130,690]), label('前往上一次作战',[970,530,1180,560])]


class TerminalEntryTests(unittest.TestCase):
    def setUp(self):
        for target in ('check_stop', '_pause', 'log'):
            p=patch.object(terminal,target);p.start();self.addCleanup(p.stop)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        p=patch.object(common,'DATA',self.root);p.start();self.addCleanup(p.stop)
        self.nav=Mock()

    def test_final_click_is_observed_before_failure(self):
        self.nav.observe.side_effect=[(None,home_labels())]*3+[(None,terminal_labels())]
        terminal.open_terminal(self.nav)
        self.assertEqual(self.nav.d.tap.call_count,3)
        self.assertEqual(self.nav.observe.call_count,4)

    def test_unknown_page_can_finish_loading_without_another_click(self):
        self.nav.observe.side_effect=[(None,home_labels()),(None,[]),(None,terminal_labels())]
        terminal.open_terminal(self.nav)
        self.nav.d.tap.assert_called_once()

    def test_persistent_home_is_bounded_and_has_image_and_decision_evidence(self):
        frame=np.zeros((720,1280,3),np.uint8)
        self.nav.observe.return_value=(frame,home_labels())
        with self.assertRaisesRegex(terminal.TerminalNavigationError,'已保存断点'):
            terminal.open_terminal(self.nav)
        self.assertEqual(self.nav.observe.call_count,8)
        self.assertEqual(self.nav.d.tap.call_count,3)
        self.nav.d.back.assert_not_called()
        saved=list((self.root/'screens/terminal-failures').glob('*.json'))
        self.assertEqual(len(saved),1)
        trace=read_json(saved[0])
        self.assertTrue(Path(trace['evidence']).exists())
        self.assertEqual(sum('tap' in row for row in trace['observations']),3)

    def test_home_missing_terminal_label_never_uses_blind_coordinate(self):
        self.nav.observe.return_value=(None,home_labels()[:-1])
        with self.assertRaises(terminal.TerminalNavigationError):terminal.open_terminal(self.nav)
        self.nav.d.tap.assert_not_called()

    def test_low_confidence_duplicate_and_wrong_location_buttons_are_not_clicked(self):
        for button in (dict(home_labels()[-1],score=.5),dict(home_labels()[-1],x=1170)):
            self.nav.observe.return_value=(None,home_labels()[:-1]+[button])
            with self.assertRaises(terminal.TerminalNavigationError):terminal.open_terminal(self.nav)
        self.nav.observe.return_value=(None,home_labels()+[home_labels()[-1]])
        with self.assertRaises(terminal.TerminalNavigationError):terminal.open_terminal(self.nav)
        self.nav.d.tap.assert_not_called()

    def test_home_with_terminal_words_is_not_arrival(self):
        self.assertFalse(terminal.terminal_visible(home_labels()+terminal_labels()))

    def test_lone_wrong_place_and_low_score_terminal_words_are_not_arrival(self):
        for texts in ([{'text':'主题曲'}],terminal_labels()[:1],
                      [dict(t,y=320) for t in terminal_labels()],
                      [dict(t,score=.6) for t in terminal_labels()]):
            self.assertFalse(terminal.terminal_visible(texts))
        self.assertTrue(terminal.terminal_visible(terminal_labels()))
        self.assertTrue(terminal.terminal_visible([terminal_labels()[0],label('曲谱',[290,650,345,690])]))

    def test_overlay_stops_without_click_even_with_home_behind_it(self):
        self.nav.observe.return_value=(None,home_labels()+[{'text':'放弃行动 继续行动'}])
        with self.assertRaises(terminal.TerminalNavigationError):terminal.open_terminal(self.nav)
        self.nav.d.tap.assert_not_called()
        self.assertEqual(self.nav.observe.call_count,1)

    def test_stop_and_device_errors_propagate_without_retry(self):
        for error in (InterruptedError('stop'),DeviceUnavailableError('offline')):
            self.nav.observe.side_effect=error
            with self.assertRaises(type(error)):terminal.open_terminal(self.nav)
        self.nav.d.tap.assert_not_called()

    def test_stop_before_observation_does_not_send_input(self):
        with patch.object(terminal,'check_stop',side_effect=InterruptedError('stop')):
            with self.assertRaises(InterruptedError):terminal.open_terminal(self.nav)
        self.nav.observe.assert_not_called();self.nav.d.tap.assert_not_called()

    def test_home_failure_is_round_wide_and_preserves_cause(self):
        error=RuntimeError('主页未确认');self.nav.home.side_effect=error
        with self.assertRaises(terminal.TerminalNavigationError) as raised:terminal.open_terminal(self.nav)
        self.assertIs(raised.exception.__cause__,error)
        self.nav.observe.assert_not_called()

    def test_observation_failure_is_round_wide_and_saves_reason(self):
        self.nav.observe.side_effect=RuntimeError('画面仍在加载')
        with self.assertRaisesRegex(terminal.TerminalNavigationError,'终端观察失败'):
            terminal.open_terminal(self.nav)
        self.nav.d.tap.assert_not_called()
        saved=next((self.root/'screens/terminal-failures').glob('*.json'))
        self.assertEqual(read_json(saved)['observations'][0]['error'],'画面仍在加载')


class RoundFailureTests(unittest.TestCase):
    setUp=scan_support.ScanOnlyTests.setUp

    def test_old_terminal_errors_do_not_defer_independent_chapters(self):
        report={'chapters':[{'chapter':'A','error':'终端页面未确认；主页按钮不作为到达证据'},
                            {'chapter':'B','error':'分区入口未确认'}],
                'failed_groups':{'A':{'error':'未能返回游戏主页'},'B':{'error':'分区入口未确认'}}}
        self.assertEqual(set(scanning.carried_failures(report,['A','B'])),{'B'})

    def test_shared_terminal_failure_stops_scan_without_labeling_other_groups(self):
        self.nav.enter.side_effect=terminal.TerminalNavigationError('terminal stuck')
        with self.assertRaises(terminal.TerminalNavigationError):scanning.scan_all(self.nav)
        self.nav.enter.assert_called_once_with('A');self.nav.scan.assert_not_called()
        report=read_json(self.root/'scan-report.json')
        self.assertEqual(report['state'],'error');self.assertEqual(report['active_group'],'A')
        self.assertEqual(report['failed_groups'],{});self.assertEqual(report['chapters'],[])
        self.assertEqual(read_json(self.root/'campaign-results.json'),self.budget)

    def test_index_navigation_failure_also_stops_scan_without_entering_groups(self):
        write_json(self.root/'scan-report.json',{'active_group':'B','state':'stopped'})
        with patch.object(scanning,'ensure_collection_index',side_effect=terminal.TerminalNavigationError('terminal stuck')):
            with self.assertRaises(terminal.TerminalNavigationError):scanning.scan_all(self.nav)
        self.nav.enter.assert_not_called();self.nav.scan.assert_not_called()
        self.assertEqual(read_json(self.root/'scan-report.json')['active_group'],'B')

    def test_campaign_stops_at_first_shared_failure_and_keeps_budget(self):
        old={'sanity':107,'count':5,'limit':None,'practice_count':18,'practice_limit':None}
        write_json(self.root/'campaign-results.json',{'budget':old,'groups':[]})
        runner=controller.Runner.__new__(controller.Runner)
        runner.core=Mock();runner.recover_previous_failures=Mock()
        def failed(*args,**kwargs):
            runner.phase='navigation'
            raise terminal.TerminalNavigationError('terminal stuck')
        runner.run=Mock(side_effect=failed)
        with patch.object(controller,'DATA',self.root),patch.object(controller,'check_stop'), \
             patch.object(controller,'assert_execution_unlocked'),patch.object(controller,'box_is_stale',return_value=False), \
             patch.object(controller,'settings',return_value={}),patch.object(controller,'catalogue',return_value={'profiles':{'1':{},'2':{}},'stages':{}}), \
             patch.object(controller,'log'),patch.object(controller,'export_coverage'):
            with self.assertRaises(terminal.TerminalNavigationError):runner.run_all(resume=True)
        self.assertEqual(runner.run.call_count,1)
        report=read_json(self.root/'campaign-results.json')
        self.assertEqual(report['state'],'error');self.assertEqual(report['active_group'],'1')
        self.assertEqual(report['budget'],old)


if __name__=='__main__':unittest.main()
