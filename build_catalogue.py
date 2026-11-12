"""Compile a local progression catalogue from public client tables; no account data."""
import hashlib,json,re
from collections import Counter,defaultdict
from pathlib import Path
from autostory.common import ROOT,MAA,now,write_json

def main():
    source=ROOT/'research/gamedata'
    tables={n:json.loads((source/(n+'.json')).read_text('utf-8')) for n in ('stage_table','zone_table','retro_table')}
    retro=tables['retro_table'];zones=tables['zone_table']['zones']
    maps=json.loads((MAA/'resource/Arknights-Tile-Pos/overview.json').read_text('utf-8'))
    map_codes={m['code'] for m in maps.values()}
    inputs={**tables['stage_table']['stages'],**retro['stageList']}
    activity_codes=defaultdict(Counter)
    for s in retro['stageList'].values():
        rid=retro['zoneToRetro'].get(s['zoneId'])
        if rid and not s.get('isStoryOnly') and s.get('code'):
            activity_codes[rid][s['code'].split('-')[0]]+=1
    rows={};profiles={};story=[]
    for sid,s in inputs.items():
        zid=s['zoneId'];rid=retro['zoneToRetro'].get(zid);act=retro['retroActList'].get(rid,{})
        is_main=s['stageType']=='MAIN' and (zid.startswith('main_') or zid.startswith('permanent_main_'))
        is_side=bool(act) and not zid.startswith('permanent_main_')
        if not (is_main or is_side):continue
        code=s.get('code','')
        if not code:continue
        digits=re.search(r'\d+',code)
        if is_main:
            if not digits:continue
            chapter=(zid.split('_')[1] if zid.startswith('main_') else str(14+int(zid.split('_')[2])))
            group=chapter;title=f'主线第 {chapter} 章'
        else:
            title=act['name'];chapter=activity_codes[rid].most_common(1)[0][0]
            group_match=re.match(r'^([A-Z]+(?:-[A-Z]+)?)-?\d+',code)
            group=group_match.group(1) if group_match else re.sub(r'-?\d+$','',code)
        zone_name=zones.get(zid,{}).get('zoneNameSecond') or zones.get(zid,{}).get('zoneNameFirst') or ''
        if s.get('isStoryOnly') or not s.get('levelId'):
            story.append({'stage_id':sid,'code':code,'chapter':chapter,'title':title,'zone':zone_name});continue
        mode=('raid' if s.get('difficulty')=='FOUR_STAR' else
              'six_star' if '#s' in sid else
              'tough' if s.get('diffGroup')=='TOUGH' or sid.startswith('tough_') else
              'easy' if s.get('diffGroup')=='EASY' or sid.startswith('easy_') else 'normal')
        row={'stage_id':sid,'code':code,'mode':mode,'chapter':chapter,'group':group,'title':title,
             'kind':'main' if is_main else 'side','zone':zone_name,'zone_id':zid,'name':s.get('name',''),
             'cost':s.get('apCost'),'can_practice':bool(s.get('canPractice')),'predefined':bool(s.get('isPredefined')),
             'prerequisites':s.get('unlockCondition',[]),'maa_map':code in map_codes,
             'level_id':s.get('levelId'),'practice_cost':s.get('practiceTicketCost')}
        rows[code+':'+mode]=row
        profile=profiles.setdefault(group,{'title':title,'base':chapter,'kind':row['kind'],'zones':[],'codes':[]})
        if zone_name and zone_name not in profile['zones']:profile['zones'].append(zone_name)
        if code not in profile['codes']:profile['codes'].append(code)
    result={'built':now(),'source':'https://github.com/Kengxxiao/ArknightsGameData/tree/master/zh_CN/gamedata/excel',
            'source_hashes':{n:hashlib.sha256((source/(n+'.json')).read_bytes()).hexdigest() for n in tables},
            'profiles':profiles,'stages':rows,'story_nodes':story}
    write_json(ROOT/'data/catalogue.json',result)
    print({'chapters':len({(r['kind'],r['chapter']) for r in rows.values()}),'groups':len(profiles),
           'stage_variants':len(rows),'battle_codes':len({r['code'] for r in rows.values()}),'story_nodes':len(story)})

if __name__=='__main__':main()
