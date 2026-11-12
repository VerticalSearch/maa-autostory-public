"""Preserve the two evidenced legacy trials and convert the old exact failure."""
import copy
import hashlib
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
from autostory.runtime_state import TaskLock
from autostory.attempts import AttemptHistory,roster_fingerprint,team_fingerprint,job_fingerprint
from autostory.jobs import match_job
from autostory.run_ledger import validate_stages


def main():
    lock=TaskLock(DATA/'alltasks.lock')
    if not lock.acquire():raise RuntimeError('Worker active; migration deferred')
    try:
        campaign=read_json(DATA/'campaign-results.json');old=read_json(DATA/'retry-migration.json')
        history=AttemptHistory()
        candidates=[DATA/'operators.json',*sorted((DATA/'backups').glob('OperBox*.json'),reverse=True)]
        box=next((read_json(path) for path in candidates if roster_fingerprint(read_json(path))==old['roster_sha']),None)
        if box is None:raise RuntimeError('Original snapshot evidence missing')
        job_path=DATA/'jobs/70118.json'
        if hashlib.sha256(job_path.read_bytes()).hexdigest()!=old['source_file_sha256']:raise RuntimeError('Original job changed')
        doc=read_json(job_path);match=match_job(doc,box)
        if match['status']=='blocked' or job_fingerprint(doc)!=old['job_sha']:raise RuntimeError('Legacy assignment changed')
        team=team_fingerprint(box,match,'6.20.1')
        fail=history.blocked('JT8-3','raid',70118,old['job_sha'],old['roster_sha'])
        if not fail:raise RuntimeError('Evidenced legacy failure missing')
        completed=[x for g in campaign['groups'] for x in g.get('outcomes',[]) if x.get('code')=='4-10' and x.get('mode')=='raid' and x.get('result')=='four_star']
        if not completed:raise RuntimeError('4-10 verified result missing')
        for name in ('campaign-results.json','attempt-history.json'):
            dest=DATA/'backups'/('before-070-'+name)
            if not dest.exists():write_json(dest,read_json(DATA/name))
        if not campaign['budget'].get('stages'):
            if campaign['budget'].get('practice_count')!=2:raise RuntimeError('Legacy count changed; cannot infer stage counts')
            campaign['budget']['stages']={
                '4-10:raid':{'practice_count':1,'candidate_limit':3,'attempts':[
                    {'job_id':86888,'phase':'verified','at':completed[-1]['at'],'migration':'evidenced-before-070'}]},
                'JT8-3:raid':{'practice_count':1,'candidate_limit':3,'attempts':[
                    {'job_id':70118,'phase':'practice_failed','at':old['failure_at'],'evidence':old['settlement'],'migration':'evidenced-before-070'}]}}
        validate_stages(campaign['budget'])
        if not history.blocked('JT8-3','raid',70118,old['job_sha'],team):
            history.record('JT8-3','raid',70118,old['job_sha'],team,'practice_failed',fail['reason'],
                {'legacy_at':fail['at'],'settlement':fail['evidence']})
        write_json(DATA/'campaign-results.json',campaign)
        report={'at':now(),'budget':campaign['budget'],'legacy_team_failure_preserved':True,
                'team_sha':team,'conversation_id':'maa-autostory-20261031-search-070'}
        write_json(DATA/'migration070.json',report)
        print('Migration complete; existing budgets retained and two stage trials attributed.')
    finally:lock.release()


if __name__=='__main__':main()
