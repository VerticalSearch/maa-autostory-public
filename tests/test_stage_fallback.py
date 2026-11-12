import unittest
from unittest.mock import Mock
import numpy as np
from autostory.navigation import Navigator

def node(code,x=200,y=200):return {'text':code,'x':x,'y':y,'score':.95}

class StageFallbackTests(unittest.TestCase):
    def setUp(self):
        self.nav=Navigator.__new__(Navigator)
        self.nav.catalog=Mock();self.nav.catalog.stages.return_value=['DV-1','DV-2']
        self.nav.catalog.code=lambda code:code if code in ('DV-1','DV-2') else None
        self.nav.v=Mock();self.nav.v.texts_zoom.return_value=[]
        self.image=np.zeros((720,1280,3),np.uint8)

    def test_exact_existing_target_does_not_retry(self):
        rows=self.nav.stage_candidates(self.image,[node('DV-1')],'DV',target='DV-1')
        self.assertEqual(rows[0]['code'],'DV-1');self.nav.v.texts_zoom.assert_not_called()

    def test_roi_fallback_recovers_exact_target_without_fuzzy_substitution(self):
        self.nav.v.texts_zoom.side_effect=[[node('DV-I')],[node('DV-2')]]
        rows=self.nav.stage_candidates(self.image,[],'DV',target='DV-2')
        self.assertEqual([r['code'] for r in rows],['DV-2'])
        self.assertEqual(self.nav.v.texts_zoom.call_count,2)

    def test_settlement_and_detail_never_trigger_map_fallback(self):
        for label in ('行动结束','开始行动'):
            self.nav.stage_candidates(self.image,[node(label)],'DV',target='DV-2')
        self.nav.v.texts_zoom.assert_not_called()

    def test_overlapping_detections_are_deduplicated(self):
        self.nav.v.texts_zoom.return_value=[node('DV-1')]
        rows=self.nav.stage_candidates(self.image,[],'DV')
        self.assertEqual(len(rows),1);self.assertEqual(self.nav.v.texts_zoom.call_count,6)

if __name__=='__main__':unittest.main()
