import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import cv2
import numpy as np
from autostory.navigation import CHAPTERS,Navigator,MapResourcesUnavailable
from autostory import story_zones as zones

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'release/evidence/flow0934'
def fixture(name):
    return json.loads((EVIDENCE/name).read_text('utf-8'))
def frame(name):return cv2.imdecode(np.fromfile(EVIDENCE/name,np.uint8),1)

class ZonesTests(unittest.TestCase):
    def test_missing_map_group_fails_before_any_navigation(self):
        nav=Navigator(Mock(),Mock());nav.observe=Mock()
        with patch('autostory.navigation.log'),self.assertRaises(MapResourcesUnavailable):nav.enter('PV-SP')
        nav.observe.assert_not_called();nav.d.tap.assert_not_called();nav.d.back.assert_not_called()

    def test_shared_pv_sp_zone_accepts_normal_map_without_sp_on_screen(self):
        groups=zones.map_groups(fixture('entry-PV.json')['texts'],CHAPTERS)
        self.assertIn('PV',groups)
        self.assertTrue(zones.same_zone('PV-SP',groups,CHAPTERS))
        self.assertFalse(zones.same_zone('PV-EX',groups,CHAPTERS))
        self.assertFalse(zones.same_zone('HE-EX',groups,CHAPTERS))

    def test_merged_labels_do_not_create_a_guessed_button(self):
        texts=fixture('entry-PV.json')['texts']
        merged=[x for x in texts if '彩车游行' in x['text']]
        self.assertIsNone(zones.text_button(merged,'PV-EX',CHAPTERS['PV-EX']))
        self.assertIsNotNone(zones.text_button(texts,'PV-EX',CHAPTERS['PV-EX']))
        for group in ('PV-EX','PV-S'):
            button=zones.text_button(fixture('entry-PV.json')['bottom'],group,CHAPTERS[group])
            self.assertIsNotNone(button)
            self.assertLess(abs(button['x']-({'PV-EX':965,'PV-S':1161}[group])),10)

    def test_outside_footer_and_ambiguous_buttons_are_rejected(self):
        button=dict(text='EX彩车游行',score=.99,x=960,y=650)
        self.assertIsNone(zones.text_button([{**button,'y':200}],'PV-EX',CHAPTERS['PV-EX']))
        self.assertIsNone(zones.text_button([button,{**button,'x':500}],'PV-EX',CHAPTERS['PV-EX']))
        self.assertIsNone(zones.text_button([{**button,'text':'EX彩车游行荒芜之舞'}],'PV-EX',CHAPTERS['PV-EX']))

    def test_detail_result_or_single_code_is_not_map_identity(self):
        texts=fixture('entry-PV.json')['texts']
        for label in ('开始行动','行动结束','放弃行动 继续行动'):
            self.assertFalse(zones.map_groups(texts+[{'text':label}],CHAPTERS))
        self.assertFalse(zones.map_groups([dict(text='PV-1',score=1,x=500,y=300)],CHAPTERS))

    def test_he_icon_requires_correct_map_and_real_pixels(self):
        positive=frame('entry-HE.png')
        button=zones.icon_button(positive,'HE-EX',{'HE'})
        self.assertIsNotNone(button)
        self.assertGreater(button['score'],.99)
        self.assertIsNone(zones.icon_button(positive,'PV-EX',{'HE'}))
        self.assertIsNone(zones.icon_button(positive,'HE-EX',{'PV'}))
        for negative in (frame('entry-PV.png'),np.zeros_like(positive),np.full_like(positive,127)):
            self.assertIsNone(zones.icon_button(negative,'HE-EX',{'HE'}))

    def test_same_zone_reuse_never_taps_and_other_story_never_taps(self):
        nav=Mock();nav.observe.return_value=(frame('entry-PV.png'),fixture('entry-PV.json')['texts'])
        with patch.object(zones,'log'):
            self.assertTrue(zones.enter_zone(nav,'PV-SP',CHAPTERS,reuse=True))
            self.assertFalse(zones.enter_zone(nav,'HE-EX',CHAPTERS,reuse=True))
        nav.d.tap.assert_not_called()

    def test_transition_has_one_tap_then_verifies_target(self):
        nav=Mock();nav.observe.return_value=(frame('entry-PV.png'),fixture('entry-PV.json')['texts'])
        nav.v.texts_zoom.return_value=fixture('entry-PV.json')['bottom']
        with patch.object(zones,'check_stop'),patch.object(zones,'log'):
            self.assertTrue(zones.enter_zone(nav,'PV-EX',CHAPTERS))
        nav.d.tap.assert_called_once()
        predicate=nav.await_state.call_args.args[0]
        self.assertFalse(predicate(None,fixture('entry-PV.json')['texts']))
        self.assertTrue(predicate(None,[dict(text='PV-EX-1',score=1,x=500,y=300),
                                        dict(text='PV-EX-2',score=1,x=800,y=300)]))
        nav.await_state.side_effect=RuntimeError('not reached')
        with patch.object(zones,'check_stop'),self.assertRaises(RuntimeError):
            zones.enter_zone(nav,'PV-EX',CHAPTERS)
        self.assertEqual(nav.d.tap.call_count,2)

if __name__=='__main__':unittest.main()
