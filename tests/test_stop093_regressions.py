import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from autostory.device import Device
from autostory.common import DeviceUnavailableError
from autostory.home_observation import navigation_regions
from autostory.navigation import Navigator,header_matches
from autostory.stage_header import contrast_header
from autostory.controller import Runner

ROOT=Path(__file__).resolve().parents[1]

def timeout_error():
    error=DeviceUnavailableError('capture timeout')
    error.__cause__=subprocess.TimeoutExpired(['adb','exec-out','screencap'],20)
    return error

class CaptureRecovery(unittest.TestCase):
    def setUp(self):
        self.d=Device.__new__(Device);self.d.serial='127.0.0.1:16384';self.d._capture_transport='raw'
        self.im=np.zeros((720,1280,3),np.uint8)
        self.png=cv2.imencode('.png',self.im)[1].tobytes()

    def test_raw_timeout_recovers_once_to_png_and_does_not_repeat_input(self):
        self.d.raw=Mock(side_effect=[timeout_error(),self.png,self.png])
        with patch('autostory.device.check_stop'),patch('autostory.device.log'):
            self.d._capture();self.d._capture()
        self.assertEqual([c.args[-1] for c in self.d.raw.call_args_list],['screencap','-p','-p'])
        self.assertEqual(self.d.raw.call_args_list[1].kwargs['timeout'],8)
        self.assertEqual(self.d._capture_transport,'png')

    def test_failed_fallback_and_stop_are_not_retried(self):
        for last in (DeviceUnavailableError('offline'),InterruptedError('stop')):
            self.d._capture_transport='raw';self.d.raw=Mock(side_effect=[timeout_error(),last])
            with patch('autostory.device.check_stop'),patch('autostory.device.log'),self.assertRaises(type(last)):
                self.d._capture()
            self.assertEqual(self.d.raw.call_count,2)

    def test_timeout_recovery_gets_one_new_stability_window(self):
        with patch('autostory.device.check_stop'),patch('autostory.motion.settle') as wait:
            def first(*args,**kwargs):
                self.d._capture_transport='png'
                return self.im,{'confirmed':False}
            wait.side_effect=lambda *a,**kw:first() if wait.call_count==1 else (self.im,{'confirmed':True})
            self.d.stable_shot()
        self.assertEqual(wait.call_count,2)

class PartialScan(unittest.TestCase):
    def test_one_failed_node_preserves_other_confirmed_pending_nodes(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.catalog=Mock()
        nav.catalog.stages.return_value=['M8-6','R8-7']
        im=np.zeros((720,1280,3),np.uint8)
        texts=[{'text':'M8-6','code':'M8-6','x':100,'y':200},{'text':'R8-7','code':'R8-7','x':400,'y':300}]
        nav.observe=Mock(return_value=(im,texts));nav.visible_stages=lambda *_:texts
        nav.close_detail=Mock();nav.set_mode=Mock()
        nav.tap_stage=Mock(side_effect=[RuntimeError('missed header'),None])
        nav.detail=Mock(return_value={'code':'R8-7','mode':'normal','state':'uncleared'})
        nav.find=Mock(return_value={'code':'M8-6','mode':'normal','state':'unknown'})
        nav.await_state=Mock(side_effect=lambda predicate,*_: (im,texts) if predicate(im,texts) else (_ for _ in ()).throw(RuntimeError('map not confirmed')))
        with patch('autostory.navigation.check_stop'),patch('autostory.navigation.read_json',return_value={}),patch('autostory.navigation.write_json'),patch('autostory.navigation.log'):
            rows=nav.scan('8',max_pages=1)
        self.assertTrue(any(r['code']=='R8-7' and r['state']=='uncleared' for r in rows))
        nav.await_state.assert_called_once()

    def test_unconfirmed_recovery_does_not_continue_to_next_node(self):
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.catalog=Mock()
        nav.catalog.stages.return_value=['M8-6','R8-7']
        im=np.zeros((720,1280,3),np.uint8)
        texts=[{'text':'M8-6','code':'M8-6','x':100,'y':200},{'text':'R8-7','code':'R8-7','x':400,'y':300}]
        nav.observe=Mock(return_value=(im,texts));nav.visible_stages=lambda *_:texts
        nav.close_detail=Mock();nav.tap_stage=Mock(side_effect=RuntimeError('missed header'))
        nav.await_state=Mock(side_effect=RuntimeError('map not confirmed'))
        with patch('autostory.navigation.check_stop'),patch('autostory.navigation.read_json',return_value={}),patch('autostory.navigation.log'):
            with self.assertRaisesRegex(RuntimeError,'map not confirmed'):nav.scan('8',max_pages=1)
        self.assertEqual(nav.tap_stage.call_count,1)

def label(text,box):
    return {'text':text,'score':.99,'x':(box[0]+box[2])/2,'y':(box[1]+box[3])/2,'box':box}

class PageAnchors(unittest.TestCase):
    def test_terminal_requires_both_controls_at_known_positions(self):
        texts=[label('终端',[75,650,140,690]),label('前往上一次作战',[970,530,1140,555])]
        self.assertEqual(navigation_regions(texts)[0],'terminal')
        self.assertIsNone(navigation_regions(texts[:1])[0])

    def test_two_map_nodes_required_and_detail_or_result_is_not_map(self):
        texts=[label('R8-7',[100,150,180,180]),label('R8-10',[600,300,700,330])]
        self.assertEqual(navigation_regions(texts)[0],'map')
        self.assertIsNone(navigation_regions(texts[:1])[0])
        for overlay in ('开始行动','开始突袭','演习结束','是否花费1源石兑换合成玉'):
            self.assertIsNone(navigation_regions(texts+[{'text':overlay}])[0])

class HeaderRegression(unittest.TestCase):
    def test_real_missed_headers_have_exact_evidence_and_reject_other_code(self):
        from autostory.vision import Vision
        from autostory.jobs import Catalog
        v=Vision(backend='cpu');nav=Navigator.__new__(Navigator);nav.v=v;nav.catalog=Catalog()
        for stamp,code,wrong in [('1793425576184915100','M8-6','M8-7'),('1793425611189940100','0-1','0-2')]:
            path=ROOT/'runtime/screens/state-timeouts'/f'{stamp}.png'
            if not path.exists():self.skipTest('Live failure fixture unavailable')
            im=cv2.imdecode(np.fromfile(path,np.uint8),1)
            texts=json.loads(path.with_suffix('.json').read_text('utf-8'))['texts']
            extra=contrast_header(v,im,code)
            self.assertTrue(header_matches(extra,code),extra)
            self.assertFalse(header_matches(extra,wrong))
            self.assertTrue(nav._detail_identity(im,texts,code))
            self.assertFalse(nav._detail_identity(im,texts,wrong))

class NavigationCircuitBreaker(unittest.TestCase):
    def test_three_consecutive_same_failures_stop_instead_of_scanning_every_group(self):
        runner=Runner.__new__(Runner);runner.core=Mock();runner.history=Mock();runner._phase='navigation'
        runner.core.refresh_box.return_value={'syncTime':'2999-01-01T00:00:00+00:00'}
        runner.run=Mock(side_effect=RuntimeError('画面仍在运动或加载，停止导航并保留截图'))
        book={'profiles':{x:{} for x in ('A','B','C','D')},'stages':{}}
        with patch('autostory.controller.catalogue',return_value=book),patch('autostory.controller.read_json',return_value={}),patch('autostory.controller.settings',return_value={}),patch('autostory.controller.write_json') as save,patch('autostory.controller.check_stop'),patch('autostory.controller.log'),patch('autostory.controller.export_coverage'),patch('autostory.controller.box_is_stale',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'连续 3 个分区'):runner.run_all()
        self.assertEqual(runner.run.call_count,3)
        self.assertTrue(any(c.args[1].get('state')=='error' for c in save.call_args_list))

if __name__=='__main__':unittest.main()
