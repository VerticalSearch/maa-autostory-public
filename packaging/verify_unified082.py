"""Verify the shipped union, not only the working tree."""
import json,sys,types,unittest
from frozen_logic_loader import ROOT,install
from PyInstaller.archive.readers import CArchiveReader


def normalize(code):
    return code.replace(co_filename='',co_consts=tuple(normalize(x) if isinstance(x,types.CodeType) else x for x in code.co_consts))


if __name__=='__main__':
    old=CArchiveReader(str(ROOT/'App-0.8.1/MAA自动推图助手.exe'))
    new=CArchiveReader(str(ROOT/'App-0.8.2/MAA自动推图助手.exe'))
    before=old.open_embedded_archive('PYZ.pyz');after=new.open_embedded_archive('PYZ.pyz')
    names=[name for name in after.toc if name.startswith('autostory.') or name in ('desktop_ui','server','worker')]
    for name in names:
        assert normalize(before.extract(name))==normalize(after.extract(name)),name
    assert normalize(__import__('marshal').loads(old.extract('desktop')))==normalize(__import__('marshal').loads(new.extract('desktop')))
    install('App-0.8.2')
    import autostory
    assert autostory.__version__=='0.8.2'
    sys.path.insert(0,str(ROOT/'tests'))
    suite=unittest.TestSuite()
    for name in ('test_attempt_history','test_retry_flow','test_runner','test_disconnect_resume',
                 'test_logic070','test_jobs_review','test_postconditions','test_motion',
                 'test_vision','test_ocr_backend','test_resilient_ocr','test_recovery_integration'):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
    with (ROOT/'runtime/tests-frozen082.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'ok':result.wasSuccessful(),'tests':result.testsRun,'unchanged_union_modules':names,
            'entry_unchanged':True,'version':autostory.__version__}
    (ROOT/'runtime/unified082-frozen-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)
