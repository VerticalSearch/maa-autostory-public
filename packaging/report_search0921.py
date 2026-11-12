"""Record exact deployed package, public-source checks and preserved campaign."""
import hashlib,json,re,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from autostory.common import DATA,read_json,write_json,now
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

manifest=read_json(DATA/'package-search0921-manifest.json')
frozen=read_json(DATA/'search0921-frozen-check.json');browser=read_json(DATA/'web0921-browser-audit.json')
assert manifest['ok'] and frozen['ok'] and browser['ok']
assert digest(ROOT/'App-0.9.21/MAA自动推图助手.exe')==manifest['exe_sha256']
assert digest(ROOT/'web/index.html')==manifest['ui_sha256']
for rel,value in manifest['source_sha256'].items():assert digest(ROOT/rel)==value,rel
tests=(DATA/'tests-source0921.log').read_text('utf-8-sig');assert re.search(r'\nOK\s*$',tests)
count=int(re.search(r'Ran (\d+) tests',tests)[1])
online=read_json(DATA/'search0921-audit/report.json');assert len(online)==5 and all(r['coverage']['complete'] for r in online)
archives=read_json(DATA/'search0921-archives.json');assert sum(r['documents'] for r in archives)==3550
deployment=read_json(DATA/'deployment-search0921.json');assert deployment['RecordsUnchanged']
resume=read_json(DATA/'search0921-resume.json');assert resume['action']=='resume-all'
assert read_json(DATA/'search0921-recovery.json')['budget_unchanged']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open('http://127.0.0.1:18741'+path,timeout=10) as response:return json.load(response)
health=get('/api/health');state=get('/api/state');assert health['version']=='0.9.21'
budget=read_json(DATA/'campaign-results.json')['budget'];counters={k:budget[k] for k in ('sanity','count','limit','practice_count','practice_limit')}
snapshot={'at':now(),'health':health,'running':state['running'],'status':state['status'],'workflow':state['workflow'],'budget':counters}
write_json(DATA/'search0921-state.json',snapshot)
config_files=['job-sources.json','data/job-source-backup.json']
sources={path:digest(ROOT/path) for path in config_files}
report=read_json(ROOT/'CORE_RELEASE_STATUS.json')
report.update(version='0.9.21',deployed_version='0.9.21',report='SEARCH_0921.md',state='Complete per-stage PRTS queries, set membership, pinned GitHub archives and local JSON imports deployed; original campaign resumed',
    exe_sha256=manifest['exe_sha256'],ui_sha256=manifest['ui_sha256'],source_tests=count,frozen_tests=frozen['tests'],browser_checks=browser['passed'],
    current_budget=counters,current_task=snapshot['status'],latest_live_result='Public online discovery verified for five chapter8 raid stages. JT8-3 job70118 formal loss was retained and settlement recovery verified. New run resumes the original budget; no new clear is claimed by this release.')
report['source_sha256'].update(manifest['source_sha256'])
report['search0921']={'manifest':'runtime/package-search0921-manifest.json','source_tests':'runtime/tests-source0921.log',
    'frozen_tests':'runtime/search0921-frozen-check.json','browser_tests':'runtime/web0921-browser-audit.json','deployment':'runtime/deployment-search0921.json',
    'online_audit':'runtime/search0921-audit/report.json','archives':'runtime/search0921-archives.json','resume':'runtime/search0921-resume.json',
    'recovery':'runtime/search0921-recovery.json','source_config_sha256':sources,'archive_documents':3550,
    'candidate_counts':[{k:r[k] for k in ('code','before','candidates','eligible')} for r in online]}
report['notes']=['One desktop entry; existing control window kept open; release/ unchanged by core owner',
    'No per-query page/result cap or eligible-first early stop; real terminal page required',
    'Historical repositories are PRTS-derived archives, not independent authoring platforms',
    'PRTS previews deduplicate by ID; full external content and executable failure history deduplicate by content and chosen team',
    'No claim of complete internet coverage, universal compatible jobs or universal stage clearing',
    'Original 200 sanity round is not replenished: resumed with 107 remaining, five paid attempts, sixteen practices',
    'Authorized 3-to5-star base skill training to7 and missing-material synthesis retained',
    f'{count} source, {frozen["tests"]} frozen and {browser["passed"]} browser checks passed']
write_json(ROOT/'CORE_RELEASE_STATUS.json',report)
readme=ROOT/'README.md';text=readme.read_text('utf-8-sig')
if not text.startswith('> **当前统一版本：0.9.21'):
    text=text.replace('> **当前统一版本：0.9.20','> **历史版本：0.9.20',1)
    header='> **当前统一版本：0.9.21（2026-11-01）**。取消作业搜索页数、条数和提前结束限制；增加内部关卡筛选、中文别名、章节作业集、两份历史仓库数据和本地 JSON 导入。实读主线/插曲/别传归档 3,550 份；先核练度、再演习，跨来源失败记录按完整脚本与实际队伍复用。485项源码、394项最终EXE、66项浏览器检查通过。原预算与进度已续跑。详见 [完整检索与历史作业补搜](SEARCH_0921.md)。\n\n'
    readme.write_text(header+text,encoding='utf-8')
print(json.dumps({'version':health['version'],'running':state['running'],'workflow':state['workflow'],'budget':counters,'source':count,'frozen':frozen['tests'],'browser':browser['passed']},ensure_ascii=False))
