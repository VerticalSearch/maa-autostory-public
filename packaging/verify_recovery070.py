"""Resume only the recorded R8-10 failed exercise; never starts a battle."""
import json
import time
from frozen_logic_loader import ROOT,install


def main():
    install('App-0.7.0')
    from autostory.common import DATA,DeviceLock,arm_stop_threshold,read_json,write_json,now
    from autostory.runtime_state import TaskLock
    from autostory.device import Device,Vision
    from autostory.navigation import Navigator,is_settlement,header_matches
    from autostory.recovery import recover_practice
    lock=TaskLock(DATA/'alltasks.lock')
    if not lock.acquire():raise RuntimeError('Worker active')
    try:
        arm_stop_threshold(time.time_ns())
        with DeviceLock():
            campaign=read_json(DATA/'campaign-results.json')
            attempt=campaign['budget']['stages']['R8-10:raid']['attempts'][-1]
            if attempt['phase']!='practice_failed' or attempt.get('start_task')!='BattleStartExercise':
                raise RuntimeError('No confirmed exercise-loss context')
            nav=Navigator(Device(),Vision())
            _,texts=nav.observe('recovery070-before')
            if not is_settlement(texts) or not header_matches(texts,'R8-10'):
                raise RuntimeError('Current screen is not the expected R8-10 result; no input sent')
            result=recover_practice(nav,'R8-10','raid','8')
            nav.d.shot(DATA/'screens/recovery070-after.png')
            attempt['recovered']=True;attempt['recovery']=result
            write_json(DATA/'campaign-results.json',campaign)
            report={'ok':True,'at':now(),'battle_started':False,'recovery':result}
            write_json(DATA/'recovery070-live-check.json',report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:lock.release()


if __name__=='__main__':main()
