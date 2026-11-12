"""Bounded live verification from shipped bytecode; never invokes formal battle."""
import hashlib
import json
from pathlib import Path
import sys
import time
from frozen_logic_loader import ROOT,install
install('App-0.7.0')
from autostory.common import DATA,DeviceLock,arm_stop_threshold,read_json,write_json,now,check_stop
from autostory.runtime_state import TaskLock,make_identity,write_claim,clear_claim
from autostory.device import Device,Vision
from autostory.core import Core
from autostory.navigation import Navigator
from autostory.controller import Runner,candidate_priority
from autostory.jobs import match_job,assigned_document
from autostory.attempts import job_fingerprint,team_fingerprint
from autostory.run_ledger import reserve_practice,transition,stage_entry
from autostory.recovery import PracticeFailure,recover_practice


def main():
    lock=TaskLock(DATA/'alltasks.lock')
    if not lock.acquire():raise RuntimeError('A worker is active')
    arm_stop_threshold(time.time_ns());core=None;identity=make_identity('run','8')
    write_claim(DATA,identity)
    write_json(DATA/'status.json',{**identity,'state':'running','message':'新版演习验收，不开始正式作战'})
    report={'at':now(),'release':'0.7.0','code':'R8-10','mode':'raid','formal_calls':0,'attempts':[]}
    campaign=read_json(DATA/'campaign-results.json');ledger=campaign['budget']
    def checkpoint():
        campaign['at']=now();write_json(DATA/'campaign-results.json',campaign)
    try:
        with DeviceLock():
            device=Device();vision=Vision();nav=Navigator(device,vision);core=Core(device)
            runner=Runner(core,nav);runner.current_prefix='8';runner.current_code='R8-10';runner.current_mode='raid'
            runner.persist_checkpoint=checkpoint
            box=core.refresh_box()
            candidates=[]
            for item in runner.store.search('R8-10',mode='raid',accept=lambda doc:match_job(doc,box)['status']!='blocked'):
                match=match_job(item['preview'],box)
                if match['status']!='blocked':candidates.append({**item,'match':match})
            report['search']=runner.store.last_search
            candidates.sort(key=candidate_priority)
            local_attempts=0
            for selected in candidates[:5]:
                check_stop()
                if local_attempts>=2 or ledger['practice_count']>=ledger['practice_limit']:break
                entry=stage_entry(ledger,'R8-10:raid',3)
                if entry['practice_count']>=entry['candidate_limit']:break
                path,doc,_=runner.store.download(selected,'R8-10',mode='raid')
                match=match_job(doc,box)
                if match['status']=='blocked':continue
                box,_=runner.inspector.inspect(box,match)
                match=match_job(doc,box)
                if match['status']=='blocked':
                    report['attempts'].append({'job':selected['id'],'rejected':match['reasons']});continue
                assigned=assigned_document(doc,match);job_sha=job_fingerprint(assigned)
                team_sha=team_fingerprint(box,match,runner.execution_context)
                if runner.history.blocked('R8-10','raid',selected['id'],job_sha,team_sha):continue
                nav.enter('8');row=nav.find('R8-10',mode='raid')
                if row['state']=='cleared':
                    report.update(skipped=True,reason='Target already cleared');break
                write_json(path,assigned)
                runner.active_attempt=reserve_practice(ledger,'R8-10:raid',3,selected['id'],job_sha,team_sha)
                local_attempts+=1;checkpoint()
                try:
                    result=runner.battle('R8-10',path,trial=True,mode='raid')
                except PracticeFailure as exc:
                    transition(runner.active_attempt,'practice_failed',evidence=exc.evidence);checkpoint()
                    runner.history.record('R8-10','raid',selected['id'],job_sha,team_sha,'practice_failed',str(exc),exc.evidence)
                    attempt={'job':selected['id'],'result':'practice_failed','evidence':exc.evidence}
                    report['attempts'].append(attempt)
                    recovered=recover_practice(nav,'R8-10','raid','8')
                    transition(runner.active_attempt,'practice_failed',recovered=True);checkpoint()
                    attempt['recovery']=recovered
                    continue
                transition(runner.active_attempt,'practice_passed',evidence=str(runner.last_result_evidence));checkpoint()
                report['attempts'].append({'job':selected['id'],'result':result,'evidence':str(runner.last_result_evidence)})
                report['ok']=True;break
            report.setdefault('ok',False)
            report['budget']=ledger
            write_json(DATA/'status.json',{**identity,'state':'done','finished':now(),'message':'演习验收已结束，未开始正式作战'})
    except Exception as exc:
        report.update(ok=False,error=repr(exc),budget=ledger)
        write_json(DATA/'status.json',{**identity,'state':'error','finished':now(),'message':'演习验收停止：'+str(exc)})
        raise
    finally:
        if core:core.close()
        checkpoint();write_json(DATA/'practice070-live-check.json',report)
        clear_claim(DATA,identity['run_id']);lock.release()
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
