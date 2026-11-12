"""Focused tests for the standalone resilient OCR candidate.

Native exception classes are the real pybind classes from the locally installed
onnxruntime.  The RapidOCR wrapper is the real installed class.  No models,
sessions, inference, or network calls are used.
"""
import threading
import time
import unittest

import onnxruntime.capi.onnxruntime_pybind11_state as ort_state
from rapidocr_onnxruntime.utils.infer_engine import ONNXRuntimeError

from autostory.resilient_ocr import OCRBackendChanged, ResilientOCR


def with_cause(error, underlying):
    error.__cause__ = underlying
    return error


class Engine:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, image):
        self.calls.append(image)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Factory:
    def __init__(self, engine=None, report=None, error=None):
        self.engine = engine
        self.report = {'active': 'cpu', 'device_id': 0} if report is None else report
        self.error = error
        self.calls = []

    def __call__(self, backend, device_id):
        self.calls.append((backend, device_id))
        if self.error is not None:
            raise self.error
        return self.engine, dict(self.report)


class ResilientOcrTests(unittest.TestCase):
    def test_actual_ort_native_errors_switch(self):
        for native in (ort_state.Fail, ort_state.RuntimeException,
                       ort_state.EPFail):
            with self.subTest(native=native.__name__):
                failure = native('DML runtime failure')
                factory = Factory(Engine('ok'))
                ocr = ResilientOCR(Engine(failure), {'active': 'directml'},
                                   factory=factory)
                with self.assertRaises(OCRBackendChanged) as caught:
                    ocr('frame')
                self.assertIs(caught.exception.original_error, failure)
                self.assertEqual(factory.calls, [('cpu', 0)])
                self.assertEqual(ocr.backend, 'cpu')

    def test_rapidocr_wrapper_with_native_cause_switches(self):
        failure = with_cause(
            ONNXRuntimeError('traceback with DmlFusedNode'),
            ort_state.RuntimeException('DML runtime failure'))
        factory = Factory(Engine('ok'), {'active': 'cpu', 'device_id': 3})
        ocr = ResilientOCR(Engine(failure), {'active': 'directml-detector',
                                             'device_id': 3},
                           factory=factory)
        with self.assertRaises(OCRBackendChanged) as caught:
            ocr('frame')
        self.assertIs(caught.exception.original_error, failure)
        self.assertIs(caught.exception.original_error.__cause__,
                      failure.__cause__)
        self.assertIn('ONNXRuntimeError',
                      caught.exception.report['runtime_fallback_reason'])
        self.assertEqual(factory.calls, [('cpu', 3)])

    def test_invalid_argument_and_its_wrapper_are_excluded(self):
        failures = [
            ort_state.InvalidArgument('bad input shape'),
            with_cause(ONNXRuntimeError('traceback'),
                       ort_state.InvalidArgument('bad input shape')),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                factory = Factory(Engine('ok'))
                ocr = ResilientOCR(Engine(failure), {'active': 'directml'},
                                   factory=factory)
                with self.assertRaises(type(failure)) as caught:
                    ocr('frame')
                self.assertIs(caught.exception, failure)
                self.assertEqual(factory.calls, [])
                self.assertFalse(ocr.switched)

    def test_programming_error_anywhere_in_cause_chain_blocks_switch(self):
        for underlying in (ValueError('bad value'), TypeError('bad type'),
                           InterruptedError('stop')):
            with self.subTest(underlying=type(underlying).__name__):
                failure = with_cause(ort_state.Fail('gpu failure'), underlying)
                factory = Factory(Engine('ok'))
                ocr = ResilientOCR(Engine(failure), {'active': 'directml'},
                                   factory=factory)
                with self.assertRaises(ort_state.Fail) as caught:
                    ocr('frame')
                self.assertIs(caught.exception, failure)
                self.assertEqual(factory.calls, [])
                self.assertFalse(ocr.switched)

    def test_text_on_arbitrary_exception_or_wrapper_is_not_enough(self):
        failures = [
            Exception('ONNXRuntimeError: DmlFusedNode failed'),
            ONNXRuntimeError('ONNXRuntimeError: DmlFusedNode failed'),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                factory = Factory(Engine('ok'))
                ocr = ResilientOCR(Engine(failure), {'active': 'directml'},
                                   factory=factory)
                with self.assertRaises(type(failure)) as caught:
                    ocr('frame')
                self.assertIs(caught.exception, failure)
                self.assertEqual(factory.calls, [])
                self.assertFalse(ocr.switched)

    def test_legacy_runtime_error_explicit_marker_switches(self):
        failure = RuntimeError('ONNXRuntimeError: DmlFusedNode failed')
        factory = Factory(Engine('ok'))
        ocr = ResilientOCR(Engine(failure), {'active': 'directml'},
                           factory=factory)
        with self.assertRaises(OCRBackendChanged):
            ocr('frame')
        self.assertEqual(factory.calls, [('cpu', 0)])

    def test_plain_runtime_error_without_marker_does_not_switch(self):
        failure = RuntimeError('onnxruntime is unavailable')
        factory = Factory(Engine('ok'))
        ocr = ResilientOCR(Engine(failure), {'active': 'directml'},
                           factory=factory)
        with self.assertRaises(RuntimeError) as caught:
            ocr('frame')
        self.assertIs(caught.exception, failure)
        self.assertEqual(factory.calls, [])

    def test_gpu_failure_switches_once_and_never_retries_frame(self):
        failure = ort_state.Fail('DML execution failed')
        gpu, cpu = Engine(failure), Engine('cpu-text')
        factory = Factory(cpu, {'active': 'cpu', 'device_id': 4})
        reports = []
        ocr = ResilientOCR(gpu, {'active': 'directml', 'device_id': 4},
                           factory=factory, callback=reports.append)
        with self.assertRaises(OCRBackendChanged) as caught:
            ocr('frame-1')
        self.assertEqual(gpu.calls, ['frame-1'])
        self.assertEqual(factory.calls, [('cpu', 4)])
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['active'], 'cpu')
        self.assertEqual(reports[0]['runtime_previous_backend'], 'directml')
        self.assertIn('Fail', reports[0]['runtime_fallback_reason'])
        self.assertTrue(reports[0]['runtime_fallback_at'])
        error = caught.exception
        self.assertIs(error.original_error, failure)
        self.assertIs(error.__cause__, failure)
        self.assertEqual(error.previous_backend, 'directml')
        self.assertEqual(error.report, reports[0])
        self.assertEqual(ocr.backend, 'cpu')
        self.assertTrue(ocr.switched)
        self.assertIs(ocr.last_error, failure)
        self.assertEqual(ocr('frame-2'), 'cpu-text')
        self.assertEqual(gpu.calls, ['frame-1'])
        self.assertEqual(cpu.calls, ['frame-2'])

    def test_stop_propagates_without_switch(self):
        for stop in (KeyboardInterrupt(), SystemExit(2)):
            with self.subTest(stop=type(stop).__name__):
                factory = Factory(Engine('ok'))
                ocr = ResilientOCR(Engine(stop), {'active': 'directml'},
                                   factory=factory)
                with self.assertRaises(type(stop)):
                    ocr('frame')
                self.assertEqual(factory.calls, [])
                self.assertFalse(ocr.switched)

    def test_cpu_failure_propagates_without_recovery(self):
        failure = ort_state.Fail('cpu session failed')
        factory = Factory(Engine('ok'))
        ocr = ResilientOCR(Engine(failure), {'active': 'cpu'}, factory=factory)
        with self.assertRaises(ort_state.Fail) as caught:
            ocr('frame')
        self.assertIs(caught.exception, failure)
        self.assertEqual(factory.calls, [])
        self.assertFalse(ocr.switched)

    def test_failed_factory_is_attempted_once_and_propagates(self):
        factory = Factory(error=RuntimeError('cannot create cpu engine'))
        ocr = ResilientOCR(
            Engine(ort_state.Fail('gpu-1'), ort_state.Fail('gpu-2')),
            {'active': 'directml'}, factory=factory)
        with self.assertRaisesRegex(RuntimeError, 'cannot create cpu engine'):
            ocr('frame-1')
        self.assertEqual(factory.calls, [('cpu', 0)])
        self.assertFalse(ocr.switched)
        self.assertTrue(ocr.report['runtime_fallback_failed'])
        with self.assertRaises(ort_state.Fail):
            ocr('frame-2')
        self.assertEqual(factory.calls, [('cpu', 0)])

    def test_callback_failure_still_signals_new_pixels(self):
        def broken_callback(report):
            raise RuntimeError('report sink failed')

        ocr = ResilientOCR(Engine(ort_state.Fail('gpu')), {'active': 'directml'},
                           factory=Factory(Engine('ok')),
                           callback=broken_callback)
        with self.assertRaises(OCRBackendChanged) as caught:
            ocr('frame')
        self.assertIn('runtime_callback_error', caught.exception.report)
        self.assertEqual(ocr.backend, 'cpu')

    def test_invalid_configuration_is_rejected_without_switch(self):
        with self.assertRaises(ValueError):
            ResilientOCR(lambda image: image, {'active': 'cuda'})
        with self.assertRaises(TypeError):
            ResilientOCR(None, {'active': 'cpu'})

    def test_calls_are_serialized(self):
        state = {'active': 0, 'max': 0}
        guard = threading.Lock()

        class SlowEngine:
            def __call__(self, image):
                with guard:
                    state['active'] += 1
                    state['max'] = max(state['max'], state['active'])
                time.sleep(0.01)
                with guard:
                    state['active'] -= 1
                return image

        ocr = ResilientOCR(SlowEngine(), {'active': 'cpu'})
        threads = [threading.Thread(target=ocr, args=(index,))
                   for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(state['max'], 1)


if __name__ == '__main__':
    unittest.main()
