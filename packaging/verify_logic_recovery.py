"""Acknowledge only the previously evidenced failed practice; never starts battle."""
import json
import sys
import time
from pathlib import Path
import cv2
import numpy as np
from frozen_logic_loader import ROOT,install

def main():
    exe=install()
    from autostory.common import DATA,DeviceLock,arm_stop_threshold,write_json,now,read_json
    from autostory.runtime_state import TaskLock
    from autostory.device import Device,Vision
    from autostory.navigation import Navigator
    from autostory.recovery import recover_practice,failure_screen
    lock=TaskLock(DATA/'alltasks.lock')
    if not lock.acquire():raise RuntimeError('Another task owns the device; no recovery attempted')
    try:
        arm_stop_threshold(time.time_ns())
        with DeviceLock():
            migration=read_json(DATA/'retry-migration.json',{})
            if migration.get('job')!=70118:raise RuntimeError('No verified migration evidence')
            device=Device();vision=Vision();nav=Navigator(device,vision)
            current=device.shot(DATA/'screens/recovery-live-before.png')
            reference=cv2.imdecode(np.fromfile(Path(migration['settlement'])/'00.png',np.uint8),cv2.IMREAD_COLOR)
            delta=float(np.mean(cv2.absdiff(current,reference)))
            words=vision.texts(current)
            if delta>5 or not failure_screen(words):
                report={'ok':False,'skipped':True,'reason':'Current image differs from the evidenced failed practice; no input sent',
                        'image_delta':delta,'at':now(),'frozen_modules_from':str(exe),'actions':0}
            else:
                recovery=recover_practice(nav,'JT8-3','raid','8')
                device.shot(DATA/'screens/recovery-live-after.png')
                report={'ok':True,'at':now(),'recovery':recovery,'image_delta':delta,
                        'frozen_modules_from':str(exe),'battle_started':False}
            write_json(DATA/'recovery-live-check.json',report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:lock.release()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        (ROOT/'runtime/recovery-live-check.json').write_text(json.dumps({'ok':False,'error':str(exc),
            'battle_started':False},ensure_ascii=False,indent=2),encoding='utf-8')
        raise
