"""Bounded live acceptance: refresh box, inspect raid, practice, then one formal clear."""
import hashlib
from autostory.common import DeviceLock,DATA,write_json,now,log
from autostory.device import Device,Vision
from autostory.navigation import Navigator
from autostory.core import Core
from autostory.jobs import match_job,assigned_document
from autostory.controller import Runner

def main():
    with DeviceLock():
        d=Device();v=Vision();core=Core(d);nav=Navigator(d,v,core)
        runner=Runner(core,nav);runner.current_prefix='CW-EX'
        record={'code':'CW-EX-8','mode':'raid','job':84529,'started':now(),'formal_attempts':0}
        try:
            box=core.refresh_box()
            nav.enter('CW-EX');row=nav.find('CW-EX-8',mode='raid')
            if row['state']=='cleared':raise RuntimeError('目标突袭已通关，无需重复验证')
            if row['state']!='uncleared' or row.get('cost')!=20:raise RuntimeError('目标状态或成本不符合实机验收条件')
            path,doc,_=runner.store.download({'id':84529},'CW-EX-8',mode='raid')
            match=match_job(doc,box)
            if match['status']=='blocked':raise RuntimeError(str(match['reasons']))
            write_json(path,assigned_document(doc,match))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            log('CW-EX-8 突袭 #84529：先进行真实演习')
            record['job_sha256']=digest;record['box_time']=box['syncTime']
            record['practice']=runner.battle('CW-EX-8',path,True,mode='raid')
            record['practice_at']=now()
            write_json(DATA/'raid-acceptance.json',record)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('演习后作业变化')
            record['formal_attempts']=1;write_json(DATA/'raid-acceptance.json',record)
            runner.battle('CW-EX-8',path,False,mode='raid')
            after=nav.find('CW-EX-8',mode='raid')
            if after['state']!='cleared':raise RuntimeError('突袭结算后未确认白色通关标志')
            record.update(result='cleared',finished=now(),evidence=after['evidence'])
            write_json(DATA/'raid-acceptance.json',record)
        except Exception as e:
            record.update(result='stopped',reason=str(e),finished=now())
            write_json(DATA/'raid-acceptance.json',record)
            raise
        finally:core.close()

if __name__=='__main__':main()
