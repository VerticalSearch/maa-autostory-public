import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from autostory import motion,scanning
from autostory.common import read_json,write_json,DeviceUnavailableError
from autostory.home_observation import navigation_regions
from autostory.navigation import Navigator
from autostory.scan_progress import summarize_scan
ROOT=Path(__file__).resolve().parents[1]

class MapDriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=ROOT/'runtime/scan0922/idle-map'
        cls.frames=[cv2.imdecode(np.fromfile(base/f'{i:02d}.png',np.uint8),1) for i in range(36)]
        cls.labels=read_json(base/'00.json');_,cls.rois=navigation_regions(cls.labels)
    def test_all_idle_three_frame_windows_confirm_within_deadline(self):
        for i in range(34):
            with self.subTest(i=i):
                _,report=motion.settle(Mock(side_effect=self.frames[i:i+3]),Mock(),Mock(),
                    clock=Mock(side_effect=[0,0,.15,.3]),rois=self.rois,kind='map')
                self.assertTrue(report['confirmed']);self.assertEqual(report['frames'],3)
    def test_real_pixels_shifted_one_to_thirty_pixels_are_rejected(self):
        for axis in (0,1):
            for shift in (1,2,3,10,30):
                for roi in self.rois:
                    self.assertFalse(motion.map_motion(motion.map_signature(self.frames[0],roi),
                        motion.map_signature(np.roll(self.frames[0],shift,axis=axis),roi))['stable'])
    def test_blank_loading_and_single_anchor_cannot_confirm(self):
        for value in (0,120,255):
            frame=np.full_like(self.frames[0],value)
            _,report=motion.settle(lambda:frame,Mock(),Mock(),clock=Mock(side_effect=[0,0,4]),rois=self.rois,kind='map')
            self.assertFalse(report['confirmed'])
        with self.assertRaises(ValueError):motion.settle(Mock(),Mock(),Mock(),rois=self.rois[:1],kind='map')
    def test_map_mode_keeps_deadline_and_stop_guards(self):
        _,report=motion.settle(lambda:self.frames[0],Mock(),Mock(),clock=Mock(side_effect=[0,0,1,4]),rois=self.rois,kind='map')
        self.assertFalse(report['confirmed'])
        with self.assertRaises(InterruptedError):
            motion.settle(Mock(),Mock(side_effect=InterruptedError('stop')),Mock(),rois=self.rois,kind='map')
    def test_overlay_and_wrong_page_do_not_select_map_mode(self):
        for word in ('任务失败','MISSIONFAILED','开始行动','放弃行动 继续行动','是否花费源石兑换合成玉'):
            self.assertEqual(navigation_regions(self.labels+[{'text':word}]),(None,()))
    def test_observation_uses_map_gate_then_rechecks_fresh_page(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d._needs_settle=True;nav.d._settle_rois=();nav.v.texts.side_effect=[self.labels,self.labels]
        nav.observe();nav.d.invalidate_frame.assert_called_once_with(self.rois,kind='map');nav.d.tap.assert_not_called()
        nav.v.texts.side_effect=[self.labels,[{'text':'未知页面'}]]*2
        with patch('autostory.navigation.log'),self.assertRaisesRegex(RuntimeError,'发生变化'):nav.observe()
        nav.d.tap.assert_not_called()


def book():
    return {'profiles':{'A':{},'B':{}},'stages':{
        'A-1:normal':{'group':'A','code':'A-1','mode':'normal'},
        'B-1:normal':{'group':'B','code':'B-1','mode':'normal'}}}
def record(code,state='uncleared'):
    return {'code':code,'mode':'normal','state':state,'at':'2026-11-01T00:00:00+08:00','evidence':'screen.png'}

class ScanOnlyTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for name,value in [('DATA',self.root),('catalogue',Mock(return_value=book())),('check_stop',Mock()),
                           ('log',Mock()),('export_coverage',Mock())]:
            target=patch.object(scanning,name,value);target.start();self.addCleanup(target.stop)
        target=patch.object(scanning.workflow,'update');target.start();self.addCleanup(target.stop)
        self.nav=Mock();self.nav.on_scan_stage=None
        self.budget={'sanity':107,'count':5,'practice_count':18};write_json(self.root/'campaign-results.json',self.budget)
    def set_result(self,group,state='uncleared'):
        db=read_json(self.root/'progress.json',{});row=record(group+'-1',state);db[group+'-1:normal']=row
        write_json(self.root/'progress.json',db);self.nav.on_scan_stage(row)
    def test_scan_saves_each_stage_and_never_changes_battle_budget(self):
        self.nav.scan.side_effect=lambda group,**kw:self.set_result(group)
        result=scanning.scan_all(self.nav)
        self.assertEqual(result['state'],'done');self.assertEqual(self.nav.enter.call_count,2)
        self.assertEqual(read_json(self.root/'campaign-results.json'),self.budget)
        self.assertEqual(len(read_json(self.root/'uncleared.json')['rows']),2)
        self.assertTrue((self.root/'uncleared.csv').exists());self.assertIsNone(self.nav.on_scan_stage)
        for call in self.nav.scan.call_args_list:self.assertEqual(call.kwargs,{'incremental':True,'only_missing':True})
    def test_known_uncleared_is_scanned_and_resume_targets_remaining_group(self):
        write_json(self.root/'progress.json',{'A-1:normal':record('A-1')})
        write_json(self.root/'scan-report.json',{'active_group':'B','state':'stopped'})
        self.nav.scan.side_effect=lambda group,**kw:self.set_result(group)
        scanning.scan_all(self.nav);self.nav.enter.assert_called_once_with('B')
    def test_empty_results_are_partial_not_done(self):
        result=scanning.scan_all(self.nav)
        self.assertEqual(result['state'],'partial')
        saved=read_json(self.root/'scan-report.json');self.assertEqual(saved['state'],'partial')
        self.assertEqual(saved['summary']['unseen'],2)
    def test_stop_keeps_active_group_and_completed_node(self):
        def scan(group,**kw):self.set_result(group);raise InterruptedError('stop')
        self.nav.scan.side_effect=scan
        with self.assertRaises(InterruptedError):scanning.scan_all(self.nav)
        report=read_json(self.root/'scan-report.json');self.assertEqual(report['state'],'stopped')
        self.assertEqual(report['active_group'],'A');self.assertEqual(report['summary']['confirmed'],1)
        self.nav.home.assert_not_called();self.assertIsNone(self.nav.on_scan_stage)
    def test_device_disconnect_does_not_recover_or_dispatch_next_chapter(self):
        self.nav.enter.side_effect=DeviceUnavailableError('offline')
        with self.assertRaises(DeviceUnavailableError):scanning.scan_all(self.nav)
        self.nav.home.assert_not_called();self.assertEqual(self.nav.enter.call_count,1)
        self.assertEqual(read_json(self.root/'scan-report.json')['state'],'error')
    def test_failed_group_continues_only_after_confirmed_home(self):
        self.nav.enter.side_effect=[RuntimeError('chapter unavailable'),None]
        self.nav.scan.side_effect=lambda group,**kw:self.set_result(group)
        result=scanning.scan_all(self.nav);self.assertEqual(result['state'],'partial');self.nav.home.assert_called_once()
        self.assertEqual(read_json(self.root/'scan-report.json')['summary']['confirmed'],1)
    def test_home_recovery_failure_stops_before_next_group(self):
        self.nav.enter.side_effect=RuntimeError('bad');self.nav.home.side_effect=RuntimeError('home unknown')
        with self.assertRaisesRegex(RuntimeError,'home unknown'):scanning.scan_all(self.nav)
        self.assertEqual(self.nav.enter.call_count,1)
    def test_unsupported_mode_remains_explicit_even_if_supported_scope_complete(self):
        data=book();data['stages']['A-1:tough']={'group':'A','code':'A-1','mode':'tough'}
        with patch.object(scanning,'catalogue',return_value=data):
            self.nav.scan.side_effect=lambda group,**kw:self.set_result(group)
            result=scanning.scan_all(self.nav)
        self.assertEqual(result['state'],'partial');self.assertIn('1 项其他难度',result['message'])
        self.assertEqual(read_json(self.root/'uncleared.json')['unsupported'],1)


class WorkerScanTests(unittest.TestCase):
    def test_worker_dispatches_scan_only_coordinator_and_returns_partial(self):
        import worker
        args=Mock(action='scan-all',chapter='ALL');result={'state':'partial','message':'未完整'}
        with patch('worker.DeviceLock'),patch('autostory.device.Device'),patch('autostory.device.Vision'),\
             patch('worker.log'),patch('autostory.common.assert_gui_idle'),\
             patch('autostory.resource_preflight.ensure_resources',return_value={'status':'healthy','net_writes':0}),\
             patch('autostory.startup.ensure_ready') as ready,\
             patch('autostory.navigation.Navigator') as nav,patch('autostory.core.Core') as core,\
             patch('autostory.scanning.scan_all',return_value=result) as scan:
            self.assertEqual(worker.execute(args),result);scan.assert_called_once_with(nav.return_value)
            ready.assert_called_once()
            core.return_value.close.assert_called_once();core.return_value.run.assert_not_called()
    def test_worker_partial_result_is_not_written_as_done(self):
        import worker
        with tempfile.TemporaryDirectory() as tmp,patch('worker.DATA',Path(tmp)),\
             patch('worker.TaskLock'),patch('worker.arm_stop_threshold'),patch('worker.write_claim'),\
             patch('worker.clear_claim'),patch('worker.check_stop'),patch('worker.workflow'),\
             patch('worker.make_identity',return_value={'run_id':'test','action':'scan-all'}),\
             patch('worker.execute',return_value={'state':'partial','message':'还有缺项'}),\
             patch('sys.argv',['worker','scan-all']):
            worker.main();status=read_json(Path(tmp)/'status.json')
            self.assertEqual(status['state'],'partial');self.assertEqual(status['message'],'还有缺项')

if __name__=='__main__':unittest.main()
