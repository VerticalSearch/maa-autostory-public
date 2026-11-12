import unittest
from unittest.mock import Mock,patch
from autostory.ocr_backend import create_ocr
from autostory.vision import Vision
import numpy as np


class BackendTests(unittest.TestCase):
    def engine(self):
        engine=Mock()
        for s in (engine.text_det.infer.session,engine.text_cls.infer.session,engine.text_rec.session.session):
            s.get_providers.return_value=['CPUExecutionProvider']
        return engine

    def test_gpu_unavailable_is_explicit_cpu_fallback(self):
        with patch('rapidocr_onnxruntime.RapidOCR',return_value=self.engine()),patch('onnxruntime.get_available_providers',return_value=['CPUExecutionProvider']):
            _,report=create_ocr('directml')
        self.assertEqual(report['active'],'cpu');self.assertIn('not installed',report['fallback_reason'])

    def test_invalid_backend_does_not_initialize_models(self):
        with patch('rapidocr_onnxruntime.RapidOCR') as factory:
            with self.assertRaises(ValueError):create_ocr('guess')
            factory.assert_not_called()

    def test_failed_gpu_session_keeps_all_cpu_sessions(self):
        engine=self.engine()
        with patch('rapidocr_onnxruntime.RapidOCR',return_value=engine),patch('onnxruntime.get_available_providers',return_value=['DmlExecutionProvider','CPUExecutionProvider']),patch('onnxruntime.InferenceSession',side_effect=RuntimeError('driver failure')):
            result,report=create_ocr('directml')
        self.assertIs(result,engine);self.assertEqual(report['active'],'cpu')
        self.assertEqual(report['fallback_reason'],'driver failure')

    def test_zoom_returns_original_coordinates_including_boxes(self):
        ocr=Mock(return_value=([([[20,40],[60,40],[60,80],[20,80]],'JT8-3',.99)],None))
        v=Vision(ocr=ocr)
        rows=v.texts_zoom(np.zeros((720,1280,3),np.uint8),(100,200,100,100),2)
        self.assertEqual(rows[0]['box'],[110,220,130,240])
        self.assertEqual((rows[0]['x'],rows[0]['y']),(120,230))

    def test_one_constant_color_channel_is_still_valid(self):
        image=np.random.default_rng(1).integers(0,255,(15,15,3),dtype=np.uint8)
        image[:,:,0]=0
        self.assertIsNone(Vision._template_problem(image))

if __name__=='__main__':unittest.main()
