"""Bounded integration check: one practice battle, never a normal battle."""
from autostory.common import DATA,DeviceLock,read_json,write_json,now
from autostory.device import Device,Vision
from autostory.core import Core
from autostory.navigation import Navigator
from autostory.jobs import JobStore,match_job,assigned_document
from autostory.controller import Runner

if __name__=='__main__':
    with DeviceLock():
        d=Device();v=Vision();n=Navigator(d,v);core=Core(d)
        try:
            n.enter('DV')
            store=JobStore();path,doc,digest=store.download({'id':41657},'DV-1')
            match=match_job(doc,read_json(DATA/'operators.json'))
            write_json(path,assigned_document(doc,match))
            r=Runner(core,n);r.current_prefix='DV'
            result=r.battle('DV-1',path,trial=True)
            write_json(DATA/'integration-trial.json',{'at':now(),'code':'DV-1','job':41657,'result':result,'normal_battles':0})
        finally:core.close()
