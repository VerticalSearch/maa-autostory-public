from functools import lru_cache
from .common import ROOT,DATA,read_json,write_json,now
import csv

@lru_cache(maxsize=1)
def catalogue():
    return read_json(ROOT/'data/catalogue.json',{'profiles':{},'stages':{},'story_nodes':[]})

def stage_info(code,mode='normal'):
    return catalogue()['stages'].get(code+':'+mode,{})

def formal_without_practice(stage):
    """A free, ordinary stage with no exercise entry: the formal run is its test.

    Only a confirmed zero sanity cost qualifies, so a loss costs nothing. Fixed
    formations, missing maps and every non-normal difficulty remain blocked.
    """
    return (isinstance(stage,dict) and stage.get('can_practice') is False and stage.get('predefined') is False
            and stage.get('maa_map') is True and stage.get('mode')=='normal'
            and type(stage.get('cost')) is int and stage['cost']==0)

def execution_blockers(stage):
    if not stage:return ['尚无此关卡的完整目录信息']
    reasons=[]
    if not stage['maa_map']:reasons.append('当前 MAA 缺少地图资源')
    if stage['mode'] not in ('normal','raid'):reasons.append('该难度执行器尚未验收')
    if stage['predefined']:reasons.append('固定编队关卡需专用作业')
    if not stage['can_practice'] and not formal_without_practice(stage):reasons.append('无演习入口，尚无验证途径')
    return reasons

def coverage():
    book=catalogue();progress=read_json(DATA/'progress.json',{})
    group_errors={}
    scan=read_json(DATA/'scan-report.json',{})
    campaign=read_json(DATA/'campaign-results.json',{})
    for report in sorted((scan,campaign),key=lambda r:r.get('at','')):
        for entry in report.get('chapters',report.get('groups',[])):
            group_errors[entry.get('chapter',entry.get('group'))]=entry.get('error',entry.get('blocked'))
    plan=read_json(DATA/'plan.json',{})
    candidates={r['code']+':'+r.get('mode','normal'):r for r in plan.get('stages',[])}
    rows=[]
    for key,s in book['stages'].items():
        record=progress.get(key,{})
        reasons=[] if record.get('state')=='cleared' else execution_blockers(s)
        if not record or record.get('state')=='unseen':reasons.append('尚未扫描账号进度')
        if record.get('state')!='cleared' and group_errors.get(s['group']):
            reasons.append('最近执行：'+group_errors[s['group']])
        if record.get('state') in ('uncleared','partial') and key in candidates:
            jobs=candidates[key].get('candidates',[])
            search=candidates[key].get('search',{})
            if search and not search.get('exhausted'):
                reasons.append('作业检索尚未完成：'+str(search.get('error') or search.get('stop_reason') or '仍有来源待查询'))
            if not jobs:reasons.append('已查询范围未找到相应难度的作业')
            elif all(j['match']['status']=='blocked' for j in jobs):reasons.append('候选作业均不符合当前干员条件')
        search=candidates.get(key,{}).get('search',{})
        rows.append({**s,'state':record.get('state','unseen'),'blockers':reasons,
                     'job_search':search,'job_count':len(candidates.get(key,{}).get('candidates',[]))})
    return {'at':now(),'chapter_count':len({(s['kind'],s['chapter']) for s in rows}),
            'stage_variants':len(rows),'battle_codes':len({s['code'] for s in rows}),
            'confirmed':sum(s['state'] not in ('unknown','unseen') for s in rows),
            'cleared':sum(s['state']=='cleared' for s in rows),'rows':rows}

def export_coverage():
    result=coverage();write_json(DATA/'coverage.json',result)
    with (DATA/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['章节','分区','关卡','难度','状态','阻塞原因'])
        for row in result['rows']:
            writer.writerow([row['chapter'],row['group'],row['code'],row['mode'],row['state'],'；'.join(row['blockers'])])
    return result
