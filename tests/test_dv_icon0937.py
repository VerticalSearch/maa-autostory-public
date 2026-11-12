import unittest
from pathlib import Path
from unittest.mock import Mock
import cv2,numpy as np
from autostory.zone_icons import match_zone_icon
from autostory.story_zones import enter_zone
from autostory.navigation import CHAPTERS

ROOT=Path(__file__).resolve().parents[1]
def image(path):return cv2.imdecode(np.fromfile(ROOT/path,np.uint8),1)
class DVIconTests(unittest.TestCase):
    def test_retained_dv_map_finds_infinity_ex_button(self):
        im=image('release/evidence/flow0937/zone-DV-EX.png')
        hit=match_zone_icon(im,'DV-EX',{'DV','DV-TR'},ROOT/'assets')
        self.assertIsNotNone(hit);self.assertGreaterEqual(hit['score'],.92)
        self.assertTrue(1125<hit['x']<1260 and 620<hit['y']<685)
    def test_wrong_context_missing_image_and_repeat_are_rejected(self):
        im=image('release/evidence/flow0937/zone-DV-EX.png')
        for context in (set(),{'SN'},{'DV-EX'},{'DV','CW'}):
            self.assertIsNone(match_zone_icon(im,'DV-EX',context,ROOT/'assets'))
        self.assertIsNone(match_zone_icon(np.zeros_like(im),'DV-EX',{'DV'},ROOT/'assets'))
        self.assertIsNone(match_zone_icon(cv2.resize(im,(640,360)),'DV-EX',{'DV'},ROOT/'assets'))
    def test_no_icon_no_click(self):
        im=image('release/evidence/flow0937/zone-DV-EX.png');im[610:685,1120:1265]=0
        self.assertIsNone(match_zone_icon(im,'DV-EX',{'DV'},ROOT/'assets'))
    def test_transition_taps_once_then_requires_real_target_codes(self):
        from unittest.mock import patch
        im=image('release/evidence/flow0937/zone-DV-EX.png')
        labels=[dict(text=c,score=.99,x=400,y=200+i*100) for i,c in enumerate(('DV-1','DV-2'))]
        nav=Mock();nav.observe.return_value=(im,labels);nav.v.texts_zoom.return_value=[]
        with patch('autostory.story_zones.check_stop'),patch('autostory.story_zones.log'):
            self.assertTrue(enter_zone(nav,'DV-EX',CHAPTERS))
        nav.d.tap.assert_called_once();predicate=nav.await_state.call_args.args[0]
        self.assertFalse(predicate(im,labels))
        target=[dict(text=c,score=.99,x=400,y=200+i*100) for i,c in enumerate(('DV-EX-1','DV-EX-2'))]
        self.assertTrue(predicate(im,target));self.assertFalse(predicate(im,target[:1]))
if __name__=='__main__':unittest.main()
