import argparse
import sys
import time
import traceback
from autostory.common import DATA, DeviceLock, DeviceUnavailableError, arm_stop_threshold, check_stop, log, now, read_json, write_json
from autostory.runtime_state import EXIT_BUSY, TaskLock, clear_claim, make_identity, write_claim
from autostory import workflow


def execute(args):
    """Run one existing action.  Device/formation/battle logic is unchanged."""
    if args.action=='plan':
        from autostory.controller import build_plan
        build_plan(args.chapter)
    elif args.action=='verify-raid':
        import verify_raid
        verify_raid.main()
    else:
        from autostory.device import Device,Vision
        from autostory.navigation import Navigator
        with DeviceLock():
            if args.action=='diagnose':
                from autostory.diagnostics import run_diagnostics
                run_diagnostics();return
            d=Device()
            # All resource writes happen under both locks, before vision/core
            # load. The local archive must match the installed core and tasks.
            from autostory.common import MAA,assert_gui_idle
            from autostory.resource_preflight import ensure_resources
            assert_gui_idle()
            resources=ensure_resources(MAA,DATA,check_stop)
            log(f'MAA 资源自检：{resources["status"]}，自动修复 {resources["net_writes"]} 项')
            v=Vision()
            from autostory.startup import ensure_ready
            ensure_ready(d,v,allow_launch=args.action!='scan-current',
                         allow_settlement=args.action in ('run','run-all','resume-all'))
            if args.action=='refresh':
                from autostory.core import Core
                core=Core(d)
                try:core.refresh_box()
                finally:core.close()
            else:
                nav=Navigator(d,v)
                nav.startup_recovery=True
                if args.action=='scan-all':
                    from autostory.core import Core
                    core=Core(d);nav.core=core
                    try:
                        from autostory.scanning import scan_all
                        return scan_all(nav)
                    finally:core.close()
                elif args.action in ('scan','scan-current'):
                    core=None
                    if args.action=='scan' and args.chapter.isdigit():
                        from autostory.core import Core
                        core=Core(d);nav.core=core
                    try:
                        if args.action=='scan':nav.enter(args.chapter)
                        nav.scan(args.chapter)
                    finally:
                        if core:core.close()
                else:
                    from autostory.core import Core
                    from autostory.controller import Runner
                    core=Core(d)
                    nav.core=core
                    try:
                        runner=Runner(core,nav)
                        from autostory.recovery import PracticeBudgetExhausted
                        try:
                            if args.action=='run-all':runner.run_all()
                            elif args.action=='resume-all':runner.run_all(resume=True)
                            else:runner.run(args.chapter)
                        except PracticeBudgetExhausted:
                            from autostory.completion import summarize_execution
                            from autostory.campaign import catalogue
                            return summarize_execution(catalogue(),read_json(DATA/'progress.json',{}),
                                [args.chapter],runner.last_ledger)
                        completion=getattr(runner,'last_completion',None)
                        if not isinstance(completion,dict) or completion.get('state') not in ('done','partial','budget_exhausted'):
                            raise RuntimeError('推图结束结果未确认，进度已保留')
                        return completion
                    finally:core.close()


def main():
    entry_ns=time.time_ns()
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['refresh','scan','scan-current','scan-all','plan','run','run-all','resume-all','diagnose','verify-raid'])
    parser.add_argument('--chapter',default='DV')
    parser.add_argument('--accepted-ns',type=int,default=None)
    args=parser.parse_args()

    # The server passes the time at which it accepted the HTTP action.  A CLI
    # invocation uses process entry time, so an older STOP is reset but a STOP
    # written after this command starts is still honoured.
    accepted_ns=args.accepted_ns if args.accepted_ns is not None else entry_ns

    # One lock covers the complete worker, including build_plan().  A worker
    # that cannot claim it must not touch status.json or STOP.
    task_lock=TaskLock(DATA/'alltasks.lock')
    if not task_lock.acquire():
        print('已有后台任务正在运行；本次未获取任务锁',file=sys.stderr,flush=True)
        return EXIT_BUSY

    # Arm the threshold only after owning the lock.  STOP is left on disk; it
    # is compared against this timestamp instead of being unlinked.
    arm_stop_threshold(accepted_ns)
    identity=make_identity(args.action,args.chapter)
    identity['accepted_ns']=accepted_ns
    run_id=identity['run_id']
    try:
        # Claim order is significant: lock/threshold -> claim file -> status.
        write_claim(DATA,identity)
        write_json(DATA/'status.json',{**identity,'state':'running'})
        workflow.begin(run_id,args.chapter)
        try:
            check_stop()
            # Bounded logs/evidence before any device input. Housekeeping must
            # never be able to stop a run, not even by failing to import.
            try:
                from autostory import housekeeping
                housekeeping.run(DATA)
            except Exception as error:
                log(f'日志与截图清理跳过：{error}')
            result=execute(args)
            state=result.get('state') if isinstance(result,dict) else None
            state=state if state in ('done','partial','budget_exhausted') else 'done'
            message=result.get('message') if isinstance(result,dict) else None
            workflow.end('finished' if state=='done' else state,message)
            write_json(DATA/'status.json',{**identity,'state':state,'finished':now(),
                **({'message':message} if message else {}),
                **({'summary':result['summary']} if isinstance(result,dict) and 'summary' in result else {})})
        except Exception as e:
            log(str(e));traceback.print_exc()
            workflow.end('stopped' if isinstance(e,InterruptedError) else 'error',str(e))
            write_json(DATA/'status.json',{**identity,'state':'stopped' if isinstance(e,InterruptedError) else 'error','message':str(e),'finished':now()})
            return 1
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        clear_claim(DATA,run_id)
        task_lock.release()


if __name__=='__main__':raise SystemExit(main())
