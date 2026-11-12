import sys
from pathlib import Path
import unittest
import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autostory.navigation import classify_stars,header_matches,Navigator
from autostory.jobs import match_job,assigned_document,Catalog

class ProgressTests(unittest.TestCase):
    def test_story_node_cannot_start_battle_navigation(self):
        with self.assertRaisesRegex(ValueError,'剧情节点'):Navigator(None,None).find('17-1')
    def test_split_stage_code(self):
        texts=[{'text':'EP-','x':950,'y':96},{'text':'8','x':995,'y':96},{'text':'OPERATION','x':890,'y':104}]
        self.assertTrue(header_matches(texts,'EP-8'))
        self.assertFalse(header_matches(texts,'EP-1'))
    def test_stage_code_different_lines_not_combined(self):
        self.assertFalse(header_matches([{'text':'EP-','x':950,'y':96},{'text':'8','x':995,'y':128}],'EP-8'))
    def test_overlapping_hyphen_is_deduplicated(self):
        self.assertTrue(header_matches([{'text':'EP-','x':950,'y':96},{'text':'-7','x':995,'y':96}],'EP-7'))
    def test_empty_picture_is_unknown_not_uncleared(self):
        self.assertEqual(classify_stars(np.zeros((720,1280,3),np.uint8))[:2],('unknown',None))
    def test_wrong_location_cyan_is_not_three_stars(self):
        im=np.zeros((720,1280,3),np.uint8)
        for x in [200,260,320]:cv2.rectangle(im,(x,90),(x+40,140),(255,190,0),-1)
        self.assertEqual(classify_stars(im)[0],'unknown')
    def test_actual_dv_completed_evidence(self):
        path=Path(__file__).resolve().parents[1]/'runtime/screens/DV-1-detail.png'
        if not path.exists():self.skipTest('实机证据文件不存在')
        im=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_COLOR)
        self.assertEqual(classify_stars(im)[:2],('cleared',3))
    def test_invalid_document_is_blocked(self):
        for d in ({'opers':None},{'groups':None},{'opers':[{}]}):
            self.assertEqual(match_job(d,{'own_opers':[]})['status'],'blocked')
    def test_blocked_assignment_cannot_execute(self):
        with self.assertRaises(ValueError):assigned_document({'opers':[]},{'status':'blocked','assignment':[]})
    def test_real_catalog_exact_matching(self):
        c=Catalog()
        self.assertEqual(c.code('act19side_01'),'DV-1')
        self.assertNotEqual(c.code('act19side_ex05'),'DV-1')
        self.assertIsNone(c.code('DV-1 this text is not a stage'))

if __name__=='__main__':unittest.main()
