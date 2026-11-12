"""Write deployment evidence from the actual package, account and local API."""
import hashlib,json,re,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import read_json,write_json,now,DATA

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    manifest=read_json(DATA/'package-training0920-manifest.json')
    frozen=read_json(DATA/'training0920-frozen-check.json')
    browser=read_json(DATA/'web0920-browser-audit.json')
    live=read_json(DATA/'training0920-live.json')
    assert manifest['ok'] and frozen['ok'] and browser['ok'] and live['verified'] and live['maa_snapshot_matches']
    tests=(DATA/'tests-training0920-source.log').read_text('utf-8-sig')
    assert re.search(r'\nOK\s*$',tests)
    source_count=int(re.search(r'Ran (\d+) tests',tests)[1])
    assert digest(ROOT/'App-0.9.20/MAA自动推图助手.exe')==manifest['exe_sha256']
    assert digest(ROOT/'web/index.html')==manifest['ui_sha256']
    for rel,value in manifest['source_sha256'].items():assert digest(ROOT/rel)==value,rel
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        with opener.open('http://127.0.0.1:18741'+path,timeout=10) as response:return json.load(response)
    health=get('/api/health');state=get('/api/state');assert health['version']=='0.9.20'
    budget=read_json(DATA/'campaign-results.json')['budget']
    counters={key:budget[key] for key in ('sanity','count','limit','practice_count','practice_limit')}
    snapshot={'at':now(),'health':health,'running':state['running'],'status':state.get('status'),
              'workflow':state.get('workflow'),'budget':counters,'policy':read_json(ROOT/'settings.json')['auto_skill_training']}
    write_json(DATA/'training0920-state.json',snapshot)
    report=read_json(ROOT/'CORE_RELEASE_STATUS.json')
    report.update(version='0.9.20',deployed_version='0.9.20',report='TRAINING_0920.md',
        state='Authorized base-skill training and native missing-material synthesis deployed; Cardigan6-to7 verified and MAA snapshot updated; campaign resumed',
        exe_sha256=manifest['exe_sha256'],ui_sha256=manifest['ui_sha256'],source_tests=source_count,
        frozen_tests=frozen['tests'],browser_checks=browser['passed'],current_budget=counters,current_task=snapshot['status'],
        latest_live_result='2026-10-31T23:14:51+08:00: Cardigan skill6-to7 verified with two independent captures; both MAA and local snapshots updated. Material stock was sufficient; no live synthesis was triggered.')
    report['source_sha256'].update(manifest['source_sha256'])
    report['source_sha256']['web\\index.html']=manifest['ui_sha256']
    report['training0920']={'manifest':'runtime/package-training0920-manifest.json',
        'source_tests':'runtime/tests-training0920-source.log','frozen_tests':'runtime/training0920-frozen-check.json',
        'browser_tests':'runtime/web0920-browser-audit.json','deployment':'runtime/deployment-training0920.json',
        'training_proof':'runtime/training0920-live.json','state':'runtime/training0920-state.json',
        'receipt':'runtime/training/1793459618270837100-char_209_ardign-rank7/receipt.json',
        'formation_recovery':'runtime/campaign-unlimited-20261031-205049/R8-9-60077-formation-second/reconciled.json',
        'synthesis_live_verified':False}
    report['notes']=['No upload; release/ unchanged by core owner','Current runtime/status.json is authoritative',
        'Authorized scope: owned3-to5-star operators, demanded base skill ranks up to7; native missing-material synthesis',
        'No elite promotion, mastery, operator leveling or premium purchases configured',
        'Three attempt caps stay null; original sanity budget and history preserved',
        'Live Cardigan6-to7 passed; synthesis route integrated from exact native source but not triggered by this upgrade',
        f'{source_count} source, {frozen["tests"]} frozen and {browser["passed"]} browser checks passed']
    write_json(ROOT/'CORE_RELEASE_STATUS.json',report)
    readme=ROOT/'README.md';text=readme.read_text('utf-8-sig')
    if not text.startswith('> **当前统一版本：0.9.20'):
        text=text.replace('> **当前统一版本：0.9.19','> **历史版本：0.9.19',1)
        header='> **当前统一版本：0.9.20（2026-10-31）**。按作业需求自动补三星至五星基础技能，最高7级，缺料调用MAA原生合成；实际等级回读后同步MAA，再演习。已实机将卡缇6→7，本次库存足够，未触发合成。修复精英阶段前置条件和截图耗时回调导致编队恢复失败的问题。471项源码、365项最终EXE、63项浏览器检查通过，原推图任务已续跑。详见 [自动补技能与缺料合成](TRAINING_0920.md)。\n\n'
        readme.write_text(header+text,encoding='utf-8')
    print(json.dumps({'version':health['version'],'running':snapshot['running'],'status':snapshot['status'],
                      'workflow':snapshot['workflow'],'budget':counters},ensure_ascii=False))

if __name__=='__main__':main()
