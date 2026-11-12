"""Import the existing evidenced JT8-3 practice loss, without touching the game."""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
from autostory.attempts import AttemptHistory,job_fingerprint,roster_fingerprint
from autostory.runtime_state import TaskLock


def main():
    lock=TaskLock(DATA/'alltasks.lock')
    if not lock.acquire():raise RuntimeError('已有任务运行，未迁移')
    try:
        status=read_json(DATA/'status.json',{})
        results=read_json(DATA/'run-results.json',[])
        loss=next((r for r in results if r.get('code')=='JT8-3' and r.get('mode')=='raid' and r.get('job')==70118 and r.get('reason')=='JT8-3 演习失败'),None)
        if not loss:raise RuntimeError('未找到对应的历史演习失败记录')
        if status.get('state')=='running':raise RuntimeError('后台任务状态仍在运行')
        evidence=DATA/'screens/settlements/1793368847-JT8-3-raid-trial'
        result=read_json(evidence/'00.json',{})
        if '任务失败' not in result.get('words','') or abs((datetime.fromisoformat(result['at'])-datetime.fromisoformat(loss['at'])).total_seconds())>5:
            raise RuntimeError('失败结算证据不匹配')
        events_path=DATA/'core-events.ndjson'
        with events_path.open('rb') as stream:
            stream.seek(max(0,events_path.stat().st_size-3_000_000))
            lines=stream.read().decode('utf-8','replace').splitlines()
        starts=[]
        for line in lines:
            try:event=json.loads(line)
            except ValueError:continue
            if '2026-10-30T21:58:04'<=event.get('time','')<=loss['at'] and event.get('msg')==20002 and event.get('details',{}).get('task')=='BattleStartExercise' and event.get('taskid')==12:
                starts.append(event)
        if not starts:raise RuntimeError('没有对应演习开始证据')
        box=read_json(DATA/'operators.json',{})
        if datetime.fromisoformat(box['syncTime'])>datetime.fromisoformat(loss['at']):
            raise RuntimeError('干员快照已改变，不能归属旧失败条件')
        job_path=DATA/'jobs/70118.json'
        if job_path.stat().st_mtime>datetime.fromisoformat(loss['at']).timestamp():
            raise RuntimeError('历史作业文件已改变，不能迁移失败')
        job_sha=job_fingerprint(read_json(job_path));roster_sha=roster_fingerprint(box)
        history=AttemptHistory()
        previous=history.blocked('JT8-3','raid',70118,job_sha,roster_sha)
        if not previous:
            history.record('JT8-3','raid',70118,job_sha,roster_sha,'practice_failed',
                '已有实机演习失败；由结算截图、开始回调及原作业迁移',str(evidence))
        cfg=read_json(ROOT/'settings.json',{})
        cfg.setdefault('max_candidates_per_stage',3)
        cfg.setdefault('max_practice_attempts_per_run',12)
        write_json(ROOT/'settings.json',cfg)
        report={'at':now(),'code':'JT8-3','mode':'raid','job':70118,'job_sha':job_sha,
                'roster_sha':roster_sha,'box_time':box['syncTime'],'failure_at':loss['at'],
                'start_evidence':starts[-1],'settlement':str(evidence),'already_present':bool(previous),
                'source_file_sha256':hashlib.sha256(job_path.read_bytes()).hexdigest()}
        write_json(DATA/'retry-migration.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:lock.release()

if __name__=='__main__':main()
