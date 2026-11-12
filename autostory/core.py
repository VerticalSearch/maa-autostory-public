from __future__ import annotations
import json
import copy
import os
import shutil
import sys
import time
from pathlib import Path
from .common import MAA, DATA, log, now, check_stop, assert_gui_idle, read_json, write_json
from . import workflow

class CoreTaskError(RuntimeError):
    """A stopped MaaCore task, with this invocation's immutable callbacks."""
    def __init__(self,task,task_id,events):
        super().__init__(f'{task} 未成功完成，保留证据，停止后续任务')
        self.task=task;self.task_id=task_id;self.events=copy.deepcopy(events)

class Core:
    def __init__(self, device):
        self.device=device
        from .resource_preflight import ensure_resources
        check_stop();assert_gui_idle()
        self.resource_preflight=ensure_resources(MAA,DATA,check_stop)
        sys.path.insert(0,str(MAA/'Python'))
        from asst.asst import Asst
        from asst.utils import InstanceOptionType
        self.events=[]
        self.cb=Asst.CallBackType(self.callback)
        self.runtime=DATA/'maa'; self.runtime.mkdir(exist_ok=True)
        if hasattr(os,'add_dll_directory'):self.dll_dir=os.add_dll_directory(str(MAA))
        if not Asst.load(path=MAA,user_dir=self.runtime):raise RuntimeError('MAA 核心资源加载失败')
        self.asst=Asst(self.cb)
        self.asst.set_instance_option(InstanceOptionType.touch_type,'maatouch')
        self.asst.set_instance_option(InstanceOptionType.kill_on_adb_exit,'0')
        self.asst.set_instance_option(InstanceOptionType.deployment_with_pause,'1')
        if not self.asst.connect(device.adb,device.serial,'General'):raise RuntimeError('MAA 核心连接模拟器失败')
        log(f'MAA {self.asst.get_version()} 已后台连接 {device.serial}')

    def callback(self,msg,details,arg):
        try:
            d=json.loads(details.decode('utf-8'))
            event={'time':now(),'msg':msg,**d}
            self.events.append(event)
            # Never forward ReportRequest: no telemetry or uploads from this helper.
            if msg != 30000:
                with (DATA/'core-events.ndjson').open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
            if d.get('what')=='OperBoxInfo':
                v=d.get('details',{})
                log(f'干员识别 {len(v.get("own_opers",[]))} 名，完成={v.get("done",False)}')
            elif msg in (10000,10001,10002):log(f'MAA 任务消息 {msg}: {d.get("taskchain","")}')
        except Exception as e:
            log(f'回调解析失败：{e}')

    def run(self, task, params, timeout=600, event_hook=None, poll_hook=None):
        check_stop();assert_gui_idle()
        # The core injects input independently of Device.tap/swipe.
        if hasattr(self,'device'):self.device.invalidate_frame()
        self.asst.stop();self.events=[]
        tid=self.asst.append_task(task,params)
        if not tid:raise RuntimeError(f'MAA 拒绝任务 {task}，见 core-events.ndjson')
        # Startup supervision is read-only; it can stop the core before another
        # component handles an installer. Never have two input owners running.
        if poll_hook:poll_hook()
        if not self.asst.start():raise RuntimeError('MAA 无法启动任务')
        start=time.monotonic()
        cursor=0
        def drain():
            nonlocal cursor
            while cursor<len(self.events):
                event=self.events[cursor];cursor+=1
                if event_hook:event_hook(event)
        try:
            while self.asst.running():
                check_stop();assert_gui_idle()
                if poll_hook:poll_hook()
                drain()
                if time.monotonic()-start>timeout:raise TimeoutError(f'{task} 超时')
                time.sleep(.3)
        finally:
            if self.asst.running():self.asst.stop()
            drain()
        errors=[e for e in self.events if e['msg'] in (0,1,10000)]
        complete=any(e['msg']==10002 and e.get('taskchain')==task for e in self.events)
        if errors or not complete:raise CoreTaskError(task,tid,self.events)
        return self.events.copy()

    def refresh_box(self):
        workflow.update('refresh',code='')
        old=read_json(MAA/'data/OperBoxData.json',{})
        if hasattr(self,'device'):
            from .device import Vision
            from .navigation import Navigator
            vision=getattr(self,'profile_vision',None)
            if vision is None:vision=Vision();self.profile_vision=vision
            # Use the guarded home route, which stops on a paused battle. Then
            # enter the list without OperBoxBegin's filtered-banner ambiguity.
            Navigator(self.device,vision).home()
        # OperBox itself keeps the last role filter and scroll position. The
        # detail locator leaves a role selected, so explicitly change role and
        # return to ALL before scanning. Every click is a MAA-recognized task.
        for tasks in (
            ['OperBox-Entry'],
            ['BattleQuickFormationExpandRoleFiltering','BattleQuickFormationExpandRole','BattleQuickFormationRoleExpanded'],
            ['BattleQuickFormationRole-Pioneer'],
            ['BattleQuickFormationRole-All','BattleQuickFormationRole-All-OCR'],
            ['OperBoxRoleTabSelect'],
        ):
            self.run('Custom',{'task_names':tasks},timeout=60)
        events=self.run('OperBox',{},timeout=900)
        results=[e['details'] for e in events if e.get('what')=='OperBoxInfo' and e.get('details',{}).get('done')]
        if not results:raise RuntimeError('MAA 未返回完整干员识别结果')
        box=results[-1];opers=box.get('own_opers',[])
        old_ids={x['id'] for x in old.get('own_opers',[]) if x.get('own')}
        ids={x['id'] for x in opers if x.get('own')}
        if not opers or len(ids)!=len(opers) or (old_ids and len(old_ids & ids)<len(old_ids)*.95):
            write_json(DATA/'rejected-box.json',box)
            raise RuntimeError('干员扫描数量或覆盖异常，未覆盖 MAA 原快照；请检查筛选条件')
        for o in opers:
            if o.get('elite') not in (0,1,2) or not 1<=o.get('level',0)<=90:raise RuntimeError('干员练度识别越界')
        from .operator_details import merge_details
        result=merge_details({'done':True,'own_opers':opers,'source':'local','syncTime':now()})
        not_seen=[o['name'] for o in old.get('own_opers',[]) if o.get('own') and o['id'] not in ids]
        previous=read_json(DATA/'box-diff.json',{}).get('not_seen',[])
        observed_names={o['name'] for o in opers}
        not_seen=sorted((set(not_seen)|set(previous))-observed_names)
        write_json(DATA/'box-diff.json',{'at':now(),'not_seen':not_seen})
        if old:write_json(DATA/'backups'/f'OperBox-{int(time.time())}.json',old)
        write_json(MAA/'data/OperBoxData.json',result)
        write_json(DATA/'operators.json',result)
        log(f'已刷新并写回 MAA 干员快照：{len(opers)} 名，{result["syncTime"]}')
        return result

    def close(self):
        if getattr(self,'asst',None):
            self.asst.stop()

