"""One bounded, non-training profile lookup through the verified MAA baseline."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'packaging'))
from frozen_logic_loader import install
baseline=install()
from autostory.common import DATA,DeviceLock,arm_stop_threshold,read_json,write_json,now
from autostory.runtime_state import TaskLock
from autostory.device import Device,Vision
from autostory.core import Core
spec=importlib.util.spec_from_file_location('autostory.operator_details',ROOT/'autostory/operator_details.py')
details=importlib.util.module_from_spec(spec);sys.modules[spec.name]=details;spec.loader.exec_module(details)


def main():
    task=TaskLock(DATA/'alltasks.lock')
    if not task.acquire():raise RuntimeError('Existing worker owns device; profile check not started')
    arm_stop_threshold(time.time_ns());core=None
    report={'at':now(),'baseline':str(baseline),'training_targets':[],'battles_started':0,
            'module_sha256':hashlib.sha256((ROOT/'autostory/operator_details.py').read_bytes()).hexdigest()}
    try:
        with DeviceLock():
            device=Device();vision=Vision();device.shot(DATA/'screens/profile070-before.png')
            core=Core(device)
            core.run('StartUp',{'client_type':'Official','start_game_enabled':True},timeout=180)
            box=read_json(DATA/'operators.json')
            row=next(x for x in box['own_opers'] if x['id']=='char_2025_shu' and x.get('own'))
            result,moved=details.OperatorInspector(core,device,vision).inspect(box,
                {'assignment':[{'id':row['id'],'operator':{'name':row['name'],'skill':3}}]})
            actual=next(x for x in result['own_opers'] if x['id']==row['id'])
            report.update(ok='3' in actual.get('observedSkillLevels',{}),navigated=moved,operator=actual)
    except Exception as exc:
        report.update(ok=False,error=repr(exc));raise
    finally:
        if core:core.close()
        write_json(DATA/'profile070-live-check.json',report)
        task.release()
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
