"""Read candidate skill levels using MAA's profile locator and templates.

The OperProgress plan intentionally contains ONLY a name. In verified MAA
6.20.1 this locates the profile; all training branches require absent targets.
No upgrade, mastery target, module unlock or resource-spending action is sent.
"""
import copy
import re
import time
from functools import lru_cache
from datetime import datetime,timezone
from .common import DATA,MAA,read_json,write_json,now,log,check_stop

TTL=1800


def base_attributes(row):
    return {k:row.get(k) for k in ('id','name','elite','level','potential')}


def fresh(record,row):
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(record['at'])).total_seconds()
    except (KeyError,TypeError,ValueError):return False
    return 0<=age<=TTL and record.get('base')==base_attributes(row)


def merge_details(box,cache=None):
    result=copy.deepcopy(box)
    cache=cache if cache is not None else read_json(DATA/'operator-details.json',{})
    for row in result.get('own_opers',[]):
        record=cache.get(row['id'],{})
        if fresh(record,row):
            for field in ('mainSkillLevel','skills','observedSkillLevels','equips'):
                if field in record:row[field]=copy.deepcopy(record[field])
    return result


@lru_cache(maxsize=1)
def character_names():
    return read_json(MAA/'resource/battle_data.json',{}).get('chars',{})


@lru_cache(maxsize=4)
def mastery_template(level):
    import cv2
    import numpy as np
    path=MAA/'resource/template/OperBox/OperFiles'/f'OperFilesSkillMaster{level}.png'
    return cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_GRAYSCALE)


def mastery_level(image,vision,roi):
    """Normalized correlation alone cannot distinguish dark zero from white M3."""
    import cv2
    import numpy as np
    candidates=[]
    for level in range(4):
        found=vision.match(image,'OperFilesSkillMaster'+str(level),roi,.80)
        if not found:continue
        template=mastery_template(level);height,width=template.shape
        x=round(found['x']-width/2);y=round(found['y']-height/2)
        crop=image[y:y+height,x:x+width]
        if crop.shape[:2]!=(height,width):continue
        gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY) if crop.ndim==3 else crop
        error=float(np.abs(gray.astype(float)-template.astype(float)).mean()/255)
        if error<=.12:candidates.append((error,level))
    candidates.sort()
    if not candidates or (len(candidates)>1 and candidates[1][0]-candidates[0][0]<.035):return None
    return candidates[0][1]


def parse_basic_profile(image,vision,row):
    """Read identity, elite stage and base skill rank without assuming mastery."""
    if not vision.match(image,'OperInformationMenu',(447,0,158,135),.90):return None
    texts=vision.texts(image)
    identity=any(x.get('text','').replace(' ','')==row['name'] for x in texts)
    import cv2
    if not identity:
        english=character_names().get(row['id'],{}).get('name_en','')
        band=cv2.resize(image[450:495,8:258],None,fx=2,fy=2)
        pieces=sorted(vision.texts(band),key=lambda item:item.get('x',0))
        normalized=lambda word:re.sub(r'[^A-Za-z0-9]','',word).casefold()
        visible=''.join(item['text'] for item in pieces if item.get('score',0)>=.90)
        identity=bool(english) and normalized(visible)==normalized(english)
    if not identity:return None
    if not vision.match(image,'OperFilesElite'+str(row['elite']),(866,235,119,88),.88):return None
    crop=cv2.resize(image[348:391,1206:1254],None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    values={int(x['text']) for x in vision.texts(crop) if re.fullmatch(r'[1-7]',x.get('text',''))}
    if len(values)!=1:return None
    return {'mainSkillLevel':values.pop()}


def parse_profile(image,vision,row):
    """Unknown/ambiguous OCR or templates never become a zero skill level."""
    basic=parse_basic_profile(image,vision,row)
    if basic is None:return None
    texts=vision.texts(image)
    rank=basic['mainSkillLevel'];masters={}
    for skill,roi in ((1,(876,328,38,33)),(2,(964,327,38,34)),(3,(1053,327,37,34))):
        level=mastery_level(image,vision,roi)
        if level is not None:
            # Nonzero mastery requires elite two and rank seven.
            if level and (row['elite']!=2 or rank!=7):return None
            if skill==3 and row['elite']<2:continue
            if skill==2 and row['elite']<1:continue
            masters[str(skill)]=rank+level
    result={'mainSkillLevel':rank,'observedSkillLevels':masters}
    contiguous=[]
    for skill in range(1,4):
        if str(skill) not in masters:break
        contiguous.append({'level':masters[str(skill)]-rank})
    if contiguous:result['skills']=contiguous
    # Read only the equipped module visible on the profile; no claim about
    # unequipped modules. Both labels must be local to the module panel.
    module_types=set();module_levels=set()
    for item in texts:
        if item.get('score',0)<.90:continue
        label=item.get('text','').replace(' ','').upper()
        if 1060<=item.get('x',-1)<=1160 and 520<=item.get('y',-1)<=610:
            found=re.fullmatch(r'[A-Z]{3}[-·]?([XYDAB])',label)
            if found:module_types.add(found[1])
        if 1150<=item.get('x',-1)<=1260 and 515<=item.get('y',-1)<=570:
            found=re.fullmatch(r'STAGE([1-3])',label)
            if found:module_levels.add(int(found[1]))
    if len(module_types)==len(module_levels)==1:
        result['equips']=[{'type':module_types.pop(),'level':module_levels.pop()}]
    return result


class OperatorInspector:
    def __init__(self,core,device,vision):
        self.core=core;self.device=device;self.vision=vision;self.tried=set()

    def inspect(self,box,match):
        selected={x['id']:x for x in match.get('assignment',[])
                  if not x.get('support') and x.get('id') is not None and x.get('operator',{}).get('skill',0)>0}
        if not selected:return box,False
        cache=read_json(DATA/'operator-details.json',{})
        changed=False;navigated=False
        for row in box.get('own_opers',[]):
            if row['id'] not in selected:continue
            slot=selected[row['id']];skill=str(slot['operator']['skill'])
            if fresh(cache.get(row['id'],{}),row) and skill in cache[row['id']].get('observedSkillLevels',{}):continue
            if row['id'] in self.tried:continue
            check_stop();self.tried.add(row['id'])
            if self.core.asst.get_version().lstrip('v')!='6.20.1':
                log('当前 MAA 版本未验证只定位档案的参数语义，技能详情保持未知');break
            folder=DATA/'screens'/'operator-details'/f'{time.time_ns()}-{row["id"]}'
            log(f'核查候选干员技能与专精：{row["name"]}')
            from . import workflow
            workflow.update('refresh',message=f'正在核查 {row["name"]} 的技能与专精，完成后自动继续选作业')
            # Never pass elite / skill_level / skill_mastery here, even as zero.
            events=self.core.run('OperProgress',{'plans':[{'name':row['name']}]},timeout=180)
            navigated=True
            summaries=[e.get('details',{}) for e in events if e.get('what')=='OperProgressSummary']
            if not summaries or summaries[-1].get('failed')!=0:
                log(f'{row["name"]} 档案定位未确认，保留未知练度');continue
            readings=[]
            for index in range(2):
                check_stop();image=self.device.shot(folder/f'{index}.png')
                readings.append(parse_profile(image,self.vision,row))
                if index==0:time.sleep(.35)
            write_json(folder/'recognition.json',{'operator':row['name'],'readings':readings,'at':now()})
            if readings[0] is None or readings[0]!=readings[1]:
                log(f'{row["name"]} 技能识别不一致，保留未知练度');continue
            cache[row['id']]={'base':base_attributes(row),'at':now(),'evidence':str(folder),**readings[0]}
            changed=True
        if changed:write_json(DATA/'operator-details.json',cache)
        result=merge_details(box,cache)
        if changed:
            old=read_json(MAA/'data/OperBoxData.json',{})
            if old:write_json(DATA/'backups'/f'OperBox-details-{time.time_ns()}.json',old)
            write_json(MAA/'data/OperBoxData.json',result)
            write_json(DATA/'operators.json',result)
        return result,navigated
