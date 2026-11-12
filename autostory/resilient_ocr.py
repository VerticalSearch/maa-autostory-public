"""Bounded runtime recovery for a non-threadsafe OCR engine (standalone).

The caller owns cache invalidation and new-pixel capture.  A successful switch
to CPU raises ``OCRBackendChanged`` exactly once; the failed screenshot is never
retried here.  Codex owns the Vision/Navigator integration.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

__all__ = ['OCRBackendChanged', 'ResilientOCR']

_BACKENDS = frozenset({'cpu', 'directml', 'directml-detector'})
_GPU_BACKENDS = _BACKENDS - {'cpu'}
_NATIVE_ERRORS = frozenset({'Fail', 'RuntimeException', 'EPFail'})
# rapidocr's ONNXRuntimeError is a bare Exception; only its native cause counts.
_INVALID_ERROR = 'InvalidArgument'
_REJECT_TYPES = (ValueError, TypeError, InterruptedError)
_MAX_CAUSE_DEPTH = 8
_LEGACY_MARKERS = ('onnxruntimeerror', 'epfail', 'dmlfusednode',
                   'dmlexecutionprovider', 'dml execution provider',
                   'dxgi_error_')


def _native_name(error):
    cls = type(error)
    module = (getattr(cls, '__module__', '') or '').lower()
    native = module == 'onnxruntime' or module.startswith('onnxruntime.')
    return cls.__name__ if native else None


def _is_runtime_failure(error):
    """True only for a whitelisted ORT native fault or known wrapper cause."""
    chain, seen = [], set()
    current = error
    while current is not None and len(chain) < _MAX_CAUSE_DEPTH:
        if id(current) in seen:
            break
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__
    native = False
    for item in chain:
        if not isinstance(item, Exception) or isinstance(item, _REJECT_TYPES):
            return False
        name = _native_name(item)
        if name == _INVALID_ERROR:
            return False
        native = native or name in _NATIVE_ERRORS
    if native:
        return True
    # Legacy only: plain RuntimeError with an explicit DML/ORT fault marker.
    # Text on arbitrary Exception/wrapper classes is deliberately ignored.
    if isinstance(error, RuntimeError):
        text = f'{type(error).__name__} {error}'.lower()
        return any(marker in text for marker in _LEGACY_MARKERS)
    return False


class OCRBackendChanged(RuntimeError):
    """Raised after a successful GPU->CPU switch; capture fresh pixels."""

    def __init__(self, report, previous_backend, reason, original_error=None):
        self.report = dict(report)
        self.previous_backend = previous_backend
        self.reason = reason
        self.original_error = original_error
        super().__init__(
            f'OCR backend changed from {previous_backend} to cpu; '
            f'capture a new screenshot before retrying ({reason})')


class ResilientOCR:
    """Serialize one engine and allow at most one GPU->CPU replacement."""

    def __init__(self, engine, report, *, factory=None, callback=None,
                 device_id=None):
        if not callable(engine):
            raise TypeError('engine must be callable')
        if not hasattr(report, 'get'):
            raise TypeError('report must be a mapping')
        backend = report.get('active')
        if backend not in _BACKENDS:
            raise ValueError(f'report active must be one of {sorted(_BACKENDS)}')
        if factory is not None and not callable(factory):
            raise TypeError('factory must be callable')
        if callback is not None and not callable(callback):
            raise TypeError('callback must be callable')
        self._engine = engine
        self._backend = backend
        self._report = dict(report)
        self._factory = factory
        self._callback = callback
        self._device_id = int(report.get('device_id', 0) if device_id is None
                              else device_id)
        self._lock = threading.RLock()
        self._recovery_attempted = False
        self._switched = False
        self._last_error = None

    @property
    def backend(self):
        return self._backend

    @property
    def report(self):
        with self._lock:
            return dict(self._report)

    @property
    def switched(self):
        return self._switched

    @property
    def last_error(self):
        return self._last_error

    def __call__(self, image):
        with self._lock:
            backend = self._backend
            try:
                return self._engine(image)
            except Exception as error:
                self._last_error = error
                if (backend not in _GPU_BACKENDS
                        or self._recovery_attempted
                        or not _is_runtime_failure(error)):
                    raise
                self._recovery_attempted = True
                self._recover(error, backend)

    def _recover(self, error, previous):
        reason = f'{type(error).__name__}: {error}'
        at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        try:
            if self._factory is None:
                raise RuntimeError('OCR GPU recovery requires a cpu factory')
            engine, report = self._factory('cpu', self._device_id)
            if not callable(engine):
                raise TypeError('factory must return a callable engine')
            report = dict(report)
            if report.get('active') != 'cpu':
                raise ValueError("factory report must have active='cpu'")
        except Exception as failure:
            self._report = {
                **self._report,
                'runtime_fallback_reason': reason,
                'runtime_fallback_at': at,
                'runtime_previous_backend': previous,
                'runtime_fallback_failed': True,
                'runtime_fallback_error': f'{type(failure).__name__}: {failure}',
            }
            raise
        report.update({
            'runtime_fallback_reason': reason,
            'runtime_fallback_at': at,
            'runtime_previous_backend': previous,
        })
        self._engine, self._backend, self._report = engine, 'cpu', report
        self._switched = True
        if self._callback is not None:
            try:
                self._callback(dict(report))
            except Exception as callback_error:
                self._report['runtime_callback_error'] = (
                    f'{type(callback_error).__name__}: {callback_error}')
        raise OCRBackendChanged(self._report, previous, reason, error) from error
