import copy,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import cv2,numpy as np
from autostory import cw_morse,dv_special
from autostory.navigation import Navigator,header_matches,CHAPTERS
from autostory.home_observation import navigation_regions
from autostory.startup_screens import classify,GAME_PACKAGE

ROOT=Path(__file__).resolve().parents[1];FIX=ROOT/'release/evidence/flow0936'
def rows(name):
    data=json.loads((FIX/name).read_text('utf-8'));return data.get('texts',[]) if isinstance(data,dict) else data
def frame(name):return cv2.imdecode(np.fromfile(FIX/name,np.uint8),1)

class CWMapTests(unittest.TestCase):
    def test_both_retained_full_maps(self):
        for name in ('cw-morse','explore/cw-enter0'):
            self.assertTrue(cw_morse.is_map(frame(name+'.png'),rows(name+'.json')))
    def test_detail_pages_are_not_maps(self):
        for i in range(7):
            self.assertFalse(cw_morse.is_map(frame(f'explore/cw-node{i}.png'),rows(f'explore/cw-node{i}.json')))
    def test_other_story_is_not_map(self):
        self.assertFalse(cw_morse.is_map(frame('dv-special.png'),rows('dv-special.json')))
    def test_missing_label_and_false_footer(self):
        image=frame('cw-morse.png');text=rows('cw-morse.json')
        for x,y,w,h in cw_morse.LABELS:
            changed=image.copy();changed[y-2:y+h+2,x-2:x+w+2]=0
            self.assertFalse(cw_morse.is_map(changed,text))
        self.assertFalse(cw_morse.is_map(image,text[:-1]))
        self.assertFalse(cw_morse.is_map(None,text))
        self.assertFalse(cw_morse.is_map(cv2.resize(image,(640,360)),text))
    def test_overlay_and_inactive_tab_are_rejected(self):
        image=frame('cw-morse.png');text=rows('cw-morse.json')
        for label in ('确认','取消','行动结束','继续行动'):
            self.assertFalse(cw_morse.is_map(image,text+[dict(text=label)]))
        image[650:690,1145:1245]=cv2.cvtColor(cv2.cvtColor(image[650:690,1145:1245],cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR)
        self.assertFalse(cw_morse.is_map(image,text))
    def test_startup_ready_needs_image_and_all_map_evidence(self):
        image=frame('cw-morse.png');text=rows('cw-morse.json')
        self.assertIsNone(navigation_regions(text)[0])
        page,_=navigation_regions(text,image);self.assertEqual(page,'cw_morse')
        self.assertEqual(classify(text,GAME_PACKAGE,page)['state'],'ready')
    def test_expected_mapping_uses_verified_headers(self):
        # The middle pair is 3-A/3-B, and the right pair is 2-A/2-B.
        self.assertEqual(cw_morse.POINTS['CW-S-3-A'],(505,429))
        self.assertEqual(cw_morse.POINTS['CW-S-2-A'],(1070,329))
        self.assertEqual(set(cw_morse.POINTS),set(CHAPTERS['CW-S']['codes']))
    def test_find_does_not_tap_unconfirmed_map(self):
        nav=Mock();nav.observe.return_value=(frame('dv-special.png'),rows('dv-special.json'))
        with self.assertRaises(RuntimeError):cw_morse.find(nav,'CW-S-1-A')
        nav.tap_stage.assert_not_called();nav.detail.assert_not_called()
    def test_normal_find_keeps_existing_missing_combat_map_barrier(self):
        nav=Navigator(Mock(),Mock())
        for code in ('CW-S-2-B','CW-S-3-B'):
            self.assertIsNone(nav.catalog.code(code))
            with patch.object(cw_morse,'find') as finder,self.assertRaises(ValueError):nav.find(code)
            finder.assert_not_called()
    def test_find_verifies_header_before_reading(self):
        nav=Mock();nav.observe.return_value=(frame('cw-morse.png'),rows('cw-morse.json'))
        nav.tap_stage.side_effect=RuntimeError('wrong detail')
        with patch.object(cw_morse,'check_stop'),self.assertRaises(RuntimeError):cw_morse.find(nav,'CW-S-3-B')
        nav.detail.assert_not_called()
    def test_scan_reads_all_seven_even_without_two_combat_maps(self):
        nav=Mock();nav.observe.return_value=(frame('cw-morse.png'),rows('cw-morse.json'))
        nav.detail.return_value=dict(code='CW-S-4',mode='raid',state='cleared')
        with patch.object(cw_morse,'find',side_effect=lambda n,c:dict(code=c,mode='normal',state='cleared')) as finder,patch('autostory.workflow.update'),patch.object(cw_morse,'log'),patch.object(cw_morse,'check_stop'):
            result=cw_morse.scan(nav)
        self.assertEqual(len(result),8);self.assertEqual(finder.call_count,7)
        self.assertIn('CW-S-2-B',[r['code'] for r in result]);self.assertIn('CW-S-3-B',[r['code'] for r in result])
    def test_incremental_only_missing_does_not_reread_complete_group(self):
        from autostory.campaign import stage_info
        db={}
        for code in cw_morse.POINTS:
            for mode in ('normal','raid'):
                if stage_info(code,mode):db[code+':'+mode]=dict(code=code,mode=mode,state='cleared',evidence='fixture',at='today')
        with patch.object(cw_morse,'read_json',return_value=db),patch.object(cw_morse,'log'),patch.object(cw_morse,'check_stop'),patch.object(cw_morse,'find') as finder:
            self.assertEqual(len(cw_morse.scan(Mock(),incremental=True,only_missing=True)),8)
            finder.assert_not_called()

class DVIntegrationTests(unittest.TestCase):
    def nav(self):
        n=Navigator(Mock(),Mock());n.observe=Mock(return_value=(frame('dv-special.png'),rows('dv-special.json')));return n
    def test_detail_identity_and_wrong_code(self):
        nav=self.nav();im,t=nav.observe()
        self.assertTrue(nav._detail_identity(im,t,'DV-S-1'));self.assertFalse(nav._detail_identity(im,t,'DV-S-2'))
    def test_unlocked_and_completed_conditions_do_not_mean_clear(self):
        nav=self.nav();nav.save_stage=Mock()
        result=nav.detail('DV-S-1','normal')
        self.assertEqual(result['state'],'unknown');self.assertIsNone(result['stars'])
        nav.save_stage.assert_called_once_with(result)
        self.assertIsNone(dv_special.identify(rows('dv-condition.json')))
    def test_wrong_difficulty_cannot_save_progress(self):
        nav=self.nav();nav.save_stage=Mock()
        with self.assertRaises(RuntimeError):nav.detail('DV-S-1','raid')
        nav.save_stage.assert_not_called()
    def test_special_exit_is_clicked_once_then_map_verified(self):
        nav=self.nav();nav.await_state=Mock()
        nav.close_detail();nav.d.tap.assert_called_once_with(1208,76,.2);nav.d.back.assert_not_called()
        predicate=nav.await_state.call_args.args[0]
        self.assertFalse(predicate(frame('dv-special.png'),rows('dv-special.json')))
        self.assertTrue(predicate(frame('explore/dv-exit0.png'),rows('dv-map.json')))
    def test_special_detail_cannot_be_a_visible_map_node(self):
        nav=self.nav();self.assertEqual(nav.visible_stages(rows('dv-special.json'),'DV-S'),[])
    def test_startup_ready_and_overlay_priority(self):
        text=rows('dv-special.json');page,_=navigation_regions(text)
        self.assertEqual(page,'dv_special');self.assertEqual(classify(text,GAME_PACKAGE,page)['state'],'ready')
        overlay=text+[dict(text='继续行动',score=1,x=600,y=400),dict(text='放弃行动',score=1,x=600,y=500)]
        self.assertIsNone(dv_special.identify(overlay));self.assertIsNone(navigation_regions(overlay)[0])

if __name__=='__main__':unittest.main()
