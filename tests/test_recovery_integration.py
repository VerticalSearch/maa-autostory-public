import unittest
from unittest.mock import Mock,patch
import numpy as np
from autostory.resilient_ocr import OCRBackendChanged,ResilientOCR
from autostory.vision import Vision
from autostory.navigation import Navigator
from autostory.controller import Runner
from onnxruntime.capi.onnxruntime_pybind11_state import Fail


class RecoveryIntegration(unittest.TestCase):
    def changed(self):return OCRBackendChanged({'active':'cpu'},'directml','injected')

    def test_backend_change_discards_cached_ocr(self):
        first=np.zeros((30,40,3),np.uint8);second=first+1
        engine=Mock(side_effect=[([],None),Fail('injected native failure')])
        wrapper=ResilientOCR(engine,{'active':'directml'},factory=lambda *_:(Mock(return_value=([],None)),{'active':'cpu'}))
        vision=Vision(ocr=wrapper)
        vision.texts(first);self.assertEqual(vision.stats()['ocr_cache_size'],1)
        with self.assertRaises(OCRBackendChanged):vision.texts(second)
        self.assertEqual(vision.stats()['ocr_cache_size'],0)
        vision.texts(first);self.assertEqual(vision.stats()['texts_cache_hits'],0)

    def test_navigation_recaptures_after_switch_without_input(self):
        old=np.zeros((20,20,3),np.uint8);new=old+1
        nav=Navigator.__new__(Navigator);nav.d=Mock();nav.v=Mock()
        nav.d.stable_shot.side_effect=[old,new]
        fresh=[{'text':'fresh','x':10,'y':10,'score':.99,'box':[1,1,19,19]}]
        nav.v.texts.side_effect=[self.changed(),fresh]
        with patch('autostory.navigation.log'):
            image,texts=nav.observe()
        self.assertIs(image,new);self.assertEqual(texts,fresh)
        nav.d.invalidate_frame.assert_called_once();nav.d.tap.assert_not_called()
        self.assertIs(nav.v.texts.call_args_list[1].args[0],new)

    def test_settlement_does_not_accept_match_on_failed_ocr_frame(self):
        runner=Runner.__new__(Runner);runner.d=Mock();runner.v=Mock()
        old=np.zeros((20,20,3),np.uint8);new=old+1
        runner.d.shot.side_effect=[old,new];runner.v.match.return_value={'score':.99}
        runner.v.texts.side_effect=[self.changed(),[{'text':'DV-1','x':500,'y':300}]]
        with patch('autostory.controller.check_stop'),patch('autostory.controller.write_json'):
            self.assertTrue(runner.result('DV-1',True))
        self.assertEqual(runner.d.shot.call_count,2);runner.d.tap.assert_not_called()

if __name__=='__main__':unittest.main()
