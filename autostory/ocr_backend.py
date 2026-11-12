"""Explicit, inspectable CPU/DirectML OCR backend; no global ORT patching."""
from __future__ import annotations


def adapters():
    """DXGI enumeration order is the device_id order used by DirectML."""
    import ctypes as c
    import sys
    import uuid
    if sys.platform != 'win32':
        return []
    class Desc(c.Structure):
        _fields_=[('Description',c.c_wchar*128),('VendorId',c.c_uint),
                  ('DeviceId',c.c_uint),('SubSysId',c.c_uint),('Revision',c.c_uint),
                  ('DedicatedVideoMemory',c.c_size_t),('DedicatedSystemMemory',c.c_size_t),
                  ('SharedSystemMemory',c.c_size_t),('LuidLow',c.c_uint),
                  ('LuidHigh',c.c_int),('Flags',c.c_uint)]
    def method(obj,index,restype,*types):
        table=c.cast(obj,c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(restype,c.c_void_p,*types)(table[index])
    guid=(c.c_ubyte*16).from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    lib=c.WinDLL('dxgi');create=lib.CreateDXGIFactory1
    create.argtypes=[c.c_void_p,c.POINTER(c.c_void_p)];create.restype=c.c_int32
    factory=c.c_void_p()
    if create(c.byref(guid),c.byref(factory))<0:return []
    result=[]
    try:
        for index in range(16):
            adapter=c.c_void_p()
            hr=method(factory,12,c.c_int32,c.c_uint,c.POINTER(c.c_void_p))(factory,index,c.byref(adapter))
            if hr<0:break
            try:
                desc=Desc()
                if method(adapter,10,c.c_int32,c.POINTER(Desc))(adapter,c.byref(desc))>=0:
                    result.append({'device_id':index,'name':desc.Description,
                                   'dedicated_bytes':desc.DedicatedVideoMemory,
                                   'software':bool(desc.Flags&2)})
            finally:method(adapter,2,c.c_uint)(adapter)
    finally:method(factory,2,c.c_uint)(factory)
    return result


def sessions(engine):
    return {'detector':engine.text_det.infer.session,
            'classifier':engine.text_cls.infer.session,
            'recognizer':engine.text_rec.session.session}


def create_ocr(backend='cpu', device_id=0):
    from rapidocr_onnxruntime import RapidOCR
    import onnxruntime as ort
    if backend not in ('cpu','directml','directml-detector'):
        raise ValueError('ocr_backend must be cpu, directml or directml-detector')
    device_id=int(device_id)
    if device_id<0:raise ValueError('ocr_device_id must be nonnegative')
    engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    report={'requested':backend,'device_id':device_id,'runtime_version':ort.__version__,
            'available_providers':ort.get_available_providers(),'fallback_reason':None}
    if backend!='cpu':
        try:
            if 'DmlExecutionProvider' not in ort.get_available_providers():
                raise RuntimeError('DirectML runtime is not installed in this Python/package')
            from rapidocr_onnxruntime.main import DEFAULT_CFG_PATH
            from rapidocr_onnxruntime.utils import read_yaml,update_model_path
            config=update_model_path(read_yaml(DEFAULT_CFG_PATH))
            targets=[('Det',engine.text_det.infer)]
            if backend=='directml':targets += [('Cls',engine.text_cls.infer),('Rec',engine.text_rec.session)]
            replacements=[]
            for key, wrapper in targets:
                options=ort.SessionOptions()
                options.enable_mem_pattern=False
                options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
                options.intra_op_num_threads=2;options.inter_op_num_threads=1
                options.log_severity_level=3
                session=ort.InferenceSession(config[key]['model_path'],sess_options=options,
                    providers=[('DmlExecutionProvider',{'device_id':device_id}),'CPUExecutionProvider'])
                if session.get_providers()[0]!='DmlExecutionProvider':
                    raise RuntimeError('DirectML session silently fell back to CPU')
                replacements.append((wrapper,session))
            # All-or-none: an initialization failure preserves the CPU engine.
            for wrapper,session in replacements:wrapper.session=session
        except Exception as error:
            report['fallback_reason']=str(error)
    report['providers']={key:value.get_providers() for key,value in sessions(engine).items()}
    report['active']='cpu' if report['providers']['detector'][0]=='CPUExecutionProvider' else backend
    return engine,report
