"""Apply the user's unlimited formal-battle setting to the idle unified app."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now,log
from autostory.controller import resume_budget
from autostory.runtime_state import TaskLock


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    settings_path=ROOT/'settings.json'
    campaign_path=DATA/'campaign-results.json'
    with TaskLock(DATA/'alltasks.lock'):
        config=read_json(settings_path,{})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base=f'http://127.0.0.1:{config["port"]}'
        def get(route):
            with opener.open(base+route,timeout=5) as response:return json.load(response)
        health=get('/api/health');state=get('/api/state')
        if (health.get('application')!='MAA-AutoStory' or health.get('version')!='0.9.18'
            or Path(health['root']).resolve()!=ROOT or state.get('running') is not False):
            raise RuntimeError('The deployed 0.9.18 service must be idle before changing its budget')
        before=read_json(campaign_path,{})
        expected_config={**config,'max_battles_per_run':None,'max_practice_attempts_per_run':None,'max_candidates_per_stage':None}
        expected_campaign=copy.deepcopy(before)
        expected_campaign['budget']=resume_budget(before['budget'],expected_config)
        tracked=[DATA/name for name in ('progress.json','operators.json','attempt-history.json','status.json')]
        tracked.append(ROOT.parent/'data/OperBoxData.json')
        account_hashes={str(path):digest(path) for path in tracked}
        backup=DATA/'backups'/f'continuous0918-budget-{time.time_ns()}'
        backup.mkdir(parents=True)
        for path in (settings_path,campaign_path):shutil.copy2(path,backup/path.name)
        write_json(settings_path,expected_config)
        write_json(campaign_path,expected_campaign)
        assert read_json(settings_path)==expected_config
        assert read_json(campaign_path)==expected_campaign
        assert all(digest(Path(path))==sha for path,sha in account_hashes.items())
        state=get('/api/state')
        assert state['settings']==expected_config and state['campaign']['budget']==expected_campaign['budget']
        report={'ok':True,'at':now(),'version':health['version'],'backup':str(backup),
                'before_budget':{k:v for k,v in before['budget'].items() if k!='stages'},
                'after_budget':{k:v for k,v in expected_campaign['budget'].items() if k!='stages'},
                'settings_only_changed':['max_battles_per_run','max_practice_attempts_per_run','max_candidates_per_stage'],
                'campaign_only_changed':['budget.limit','budget.practice_limit','budget.stages.*.candidate_limit'],'account_sha256_unchanged':account_hashes,
                'no_game_task_started':not state['running']}
        write_json(DATA/'continuous0918-budget-change.json',report)
        log(f'已取消正式、演习总次数与单关尝试次数上限；保留累计 {expected_campaign["budget"]["count"]} 次正式、'
            f'剩余本轮预算 {expected_campaign["budget"]["sanity"]} 理智及全部演习记录，可继续上轮')
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
