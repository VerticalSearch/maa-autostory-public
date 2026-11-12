import copy,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from autostory import sn_observation,startup
from autostory.home_observation import navigation_regions,navigation_texts,map_codes
from autostory.motion import settle,map_motion,map_signature
from autostory.navigation import Navigator

ROOT=Path(__file__).resolve().parents[1];FIX=ROOT/'release/evidence/flow0937'
def rows():return json.loads((FIX/'sn-full-ocr.json').read_text('utf-8'))
def tiles():return json.loads((FIX/'sn-zoom-ocr.json').read_text('utf-8'))
def frames():return [cv2.imdecode(np.fromfile(p,np.uint8),1) for p in sorted(FIX.glob('179*.png'))[-4:]]
def vision():
    v=Mock();v.texts.side_effect=lambda image:rows()
    v.texts_zoom.side_effect=lambda image,roi,scale=2:copy.deepcopy(next(t['texts'] for t in tiles() if tuple(t['roi'])==tuple(roi)))
    return v

class SNIntegrationTests(unittest.TestCase):
    def test_exact_recovery_provides_two_actual_stage_anchors(self):
        im=frames()[-1];self.assertEqual(navigation_regions(rows(),im)[0],'sn_map')
        v=vision()
        with patch('autostory.common.check_stop',return_value=None):labels=navigation_texts(v,im)
        page,regions=navigation_regions(labels,im)
        self.assertEqual(page,'map');self.assertEqual(len(regions),2)
        self.assertEqual(v.texts_zoom.call_count,1)
        self.assertIn('SN-TR-1',[r['text'] for r in labels])
        self.assertEqual(rows(),labels[:len(rows())])
    def test_all_idle_frame_windows_settle_without_threshold_changes(self):
        captured=frames();v=vision()
        with patch('autostory.common.check_stop',return_value=None):labels=navigation_texts(v,captured[-1])
        _,regions=navigation_regions(labels,captured[-1])
        for i in (0,1):
            capture=Mock(side_effect=captured[i:i+3])
            _,report=settle(capture,Mock(),Mock(),clock=lambda:0,rois=regions,kind='map')
            self.assertTrue(report['confirmed']);self.assertEqual(report['frames'],3)
    def test_one_two_and_five_pixel_motion_remain_rejected(self):
        im=frames()[-1]
        with patch('autostory.common.check_stop',return_value=None):labels=navigation_texts(vision(),im)
        _,regions=navigation_regions(labels,im)
        for axis in (0,1):
            for pixels in (1,2,5):
                for region in regions:
                    self.assertFalse(map_motion(map_signature(im,region),map_signature(np.roll(im,pixels,axis=axis),region))['stable'])
    def test_navigator_rechecks_fresh_ocr_after_settling_without_input(self):
        im=frames()[-1];d=Mock();d._needs_settle=True;d._settle_rois=()
        d.shot.return_value=im;d.stable_shot.return_value=im;v=vision();nav=Navigator(d,v)
        with patch('autostory.common.check_stop',return_value=None):_,labels=nav.observe()
        self.assertEqual(v.texts.call_count,2);self.assertEqual(v.texts_zoom.call_count,2)
        self.assertEqual(d.invalidate_frame.call_args.kwargs,dict(kind='map'))
        self.assertEqual(len(d.invalidate_frame.call_args.args[0]),2)
        self.assertEqual(navigation_regions(labels,im)[0],'map')
        d.tap.assert_not_called();d.back.assert_not_called();d.swipe.assert_not_called()
    def test_different_final_page_cannot_reuse_first_frame_labels(self):
        im=frames()[-1];d=Mock();d._needs_settle=True;d._settle_rois=()
        d.shot.return_value=im;d.stable_shot.return_value=im;v=vision()
        v.texts.side_effect=[rows(),[dict(text='加载中',score=.99,x=640,y=360)]]*2
        nav=Navigator(d,v)
        with patch('autostory.common.check_stop',return_value=None),patch('autostory.navigation.log'),self.assertRaisesRegex(RuntimeError,'发生变化'):nav.observe()
        d.tap.assert_not_called();d.back.assert_not_called();d.swipe.assert_not_called()
    def test_startup_uses_same_bounded_recognition_and_checks_focus(self):
        im=frames()[-1];d=Mock();d.shot.return_value=im
        with patch('autostory.common.check_stop',return_value=None),patch.object(startup,'check_stop'),patch.object(startup,'foreground',return_value=startup.GAME):
            _,result=startup._read(d,vision())
        self.assertEqual(result['state'],'ready');self.assertIn('SN-TR-1',[r['text'] for r in result['texts']])
        with patch('autostory.common.check_stop',return_value=None),patch.object(startup,'check_stop'),patch.object(startup,'foreground',side_effect=[startup.GAME,'com.android.settings']):
            _,result=startup._read(d,vision())
        self.assertNotEqual(result['state'],'ready');d.tap.assert_not_called()
    def test_crop_conflict_with_original_label_prevents_any_recovery(self):
        full=rows();code=next(r for r in full if r['text']=='SN-3');conflict={**code,'text':'SN-2'}
        zoom=Mock(return_value=tiles()[0]['texts']+[conflict])
        result=sn_observation.enrich(frames()[-1],full,zoom,Mock(return_value=None),map_codes())
        self.assertEqual(result,full);self.assertEqual(zoom.call_count,1)
    def test_crop_only_dialog_text_prevents_recovery(self):
        full=rows();zoom=Mock(return_value=tiles()[0]['texts']+[dict(text='确认')])
        self.assertEqual(sn_observation.enrich(frames()[-1],full,zoom,Mock(return_value=None),map_codes()),full)
        self.assertEqual(zoom.call_count,1)
if __name__=='__main__':unittest.main()
