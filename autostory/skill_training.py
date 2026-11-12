"""User-authorized, demand-driven basic skill upgrades and native synthesis."""
import copy,time
from .common import DATA,MAA,read_json,write_json,now,log,check_stop
from .operator_details import base_attributes,parse_basic_profile
from . import workflow


class TrainingUnavailableError(RuntimeError):
    """Capability rejected before any training input or resource change."""


def training_policy(config):
    policy=config.get('auto_skill_training',{})
    if not isinstance(policy,dict):raise ValueError('自动技能升级配置无效')
    if policy.get('enabled') is not True:return None
    if (policy.get('rarities')!=[3,4,5] or type(policy.get('max_skill_level')) is not int
            or policy['max_skill_level']!=7 or policy.get('synthesize_missing') is not True):
        raise ValueError('自动技能升级限已授权的三星至五星、最高7级及缺料合成')
    return policy


def training_plan(doc,box,config,matcher):
    actual=matcher(doc,box)
    if training_policy(config) is None:return actual
    rows={r['id']:r for r in box.get('own_opers',[]) if r.get('own')}
    chosen=actual
    if actual['status']=='blocked':
        hypothetical=copy.deepcopy(box)
        for row in hypothetical.get('own_opers',[]):
            if not row.get('own') or row.get('rarity') not in (3,4,5):continue
            ceiling=7 if row.get('elite',0)>=1 else 4
            if type(row.get('mainSkillLevel')) is int and row['mainSkillLevel']>=ceiling:continue
            row['mainSkillLevel']=ceiling
            row.pop('skills',None)
            row['observedSkillLevels']={k:ceiling for k in row.get('observedSkillLevels',{})}
        chosen=matcher(doc,hypothetical)
        if chosen['status']=='blocked':return actual
    plans={}
    for slot in chosen.get('assignment',[]):
        if slot.get('support') or slot.get('id') is None:continue  # Borrowed units are never trained.
        row=rows.get(slot['id'],{});required=slot.get('operator',{}).get('requirements',{}).get('skill_level',0)
        if type(required) is not int or not 2<=required<=7 or row.get('rarity') not in (3,4,5):continue
        if row.get('elite',0)<(1 if required>4 else 0):continue
        known=row.get('mainSkillLevel')
        if type(known) is int and known>=required:continue
        previous=plans.get(row['id'],{}).get('target',0)
        plans[row['id']]={'id':row['id'],'name':row['name'],'rarity':row['rarity'],'target':max(required,previous),'before':known}
    if not plans:return actual
    result=copy.deepcopy(chosen);result.update(status='training_required',training=list(plans.values()))
    result['reasons']=['需先补齐基础技能，升级后重新匹配与演习']
    return result


class SkillTrainer:
    def __init__(self,core,nav,config):
        self.core=core;self.nav=nav;self.config=config;self.attempted=set()

    def _read_rank(self,row,folder):
        readings=[]
        for index in range(2):
            check_stop();image=self.nav.d.shot(folder/f'{index}.png')
            readings.append(parse_basic_profile(image,self.nav.v,row))
            if index==0:time.sleep(.35)
        write_json(folder/'readings.json',{'at':now(),'operator':row['name'],'readings':readings})
        return readings[0] if readings[0] is not None and readings[0]==readings[1] else None

    def _sync(self,box,row,rank,evidence):
        result=copy.deepcopy(box)
        target=next(o for o in result['own_opers'] if o['id']==row['id'])
        # Verify only the base rank here; do not fabricate specialization data.
        for key in ('skills','observedSkillLevels'):target.pop(key,None)
        target['mainSkillLevel']=rank
        cache=read_json(DATA/'operator-details.json',{})
        cache[row['id']]={'base':base_attributes(row),'at':now(),'evidence':str(evidence),
                          'mainSkillLevel':rank,'observedSkillLevels':{}}
        write_json(DATA/'operator-details.json',cache)
        old=read_json(MAA/'data/OperBoxData.json',{})
        if old:write_json(DATA/'backups'/f'OperBox-training-{time.time_ns()}.json',old)
        write_json(MAA/'data/OperBoxData.json',result);write_json(DATA/'operators.json',result)
        return result

    def apply(self,box,match):
        if training_policy(self.config()) is None:raise RuntimeError('未启用已授权的技能升级策略')
        if self.core.asst.get_version().lstrip('v')!='6.20.1':
            raise TrainingUnavailableError('当前MAA版本尚未验证技能升级参数，未执行升级')
        for plan in match.get('training',[]):
            row=next((o for o in box.get('own_opers',[]) if o.get('own') and o['id']==plan['id']),None)
            target=plan.get('target');key=(plan.get('id'),target)
            if (row is None or row.get('rarity') not in (3,4,5) or type(target) is not int or not 2<=target<=7
                    or row['name']!=plan.get('name') or row.get('elite',0)<(1 if target>4 else 0)):
                raise ValueError('技能升级目标超出授权或前置条件不满足')
            if key in self.attempted:return box,False,f'{row["name"]} 本轮已尝试补技能，未重复消耗材料'
            self.attempted.add(key);check_stop()
            folder=DATA/'training'/f'{time.time_ns()}-{row["id"]}-rank{target}'
            receipt={'at':now(),'operator':row['name'],'id':row['id'],'rarity':row['rarity'],
                     'target':target,'before':copy.deepcopy(row),'state':'locating','synthesis_authorized':True}
            write_json(folder/'receipt.json',receipt)
            workflow.update('training',message=f'正在为 {row["name"]} 补技能至{target}级，缺料时按需合成')
            self.nav.home()
            self.core.run('OperProgress',{'plans':[{'name':row['name']}]},timeout=180)
            before=self._read_rank(row,folder/'before')
            if before is None:
                receipt.update(state='unverified',reason='目标档案或当前技能等级未确认');write_json(folder/'receipt.json',receipt)
                self.nav.home();return box,False,f'{row["name"]} 档案核查未通过，未执行升级'
            receipt.update(before_rank=before['mainSkillLevel'],state='ready');write_json(folder/'receipt.json',receipt)
            events=[]
            if before['mainSkillLevel']<target:
                # Native OperProgress recursively synthesizes only missing recipe
                # quantities. No elite, mastery, level or shop target is provided.
                receipt['state']='submitted';write_json(folder/'receipt.json',receipt)
                try:events=self.core.run('OperProgress',{'plans':[{'name':row['name'],'skill_level':target}]},timeout=900)
                except Exception as exc:
                    receipt.update(state='interrupted' if isinstance(exc,InterruptedError) else 'error',reason=str(exc),finished=now())
                    write_json(folder/'receipt.json',receipt)
                    write_json(folder/'events.json',getattr(exc,'events',[]))
                    raise
                write_json(folder/'events.json',[e for e in events if e.get('msg')!=30000])
            after=self._read_rank(row,folder/'after')
            if after is not None:box=self._sync(box,row,after['mainSkillLevel'],folder)
            success=after is not None and after['mainSkillLevel']>=target
            details=[e.get('details',{}) for e in events if e.get('what')=='OperProgressDetail']
            crafting=[e for e in events if e.get('what','').startswith('MaterialSynthesis')]
            receipt.update(state='verified' if success else 'not_completed',after=after,details=details,crafting=crafting,finished=now())
            write_json(folder/'receipt.json',receipt)
            self.nav.home()
            if not success:return box,False,f'{row["name"]} 未确认达到技能{target}级，已保留升级/合成记录'
            log(f'{row["name"]} 技能 {before["mainSkillLevel"]}→{after["mainSkillLevel"]} 已双次回读并同步MAA')
        return box,True,''
