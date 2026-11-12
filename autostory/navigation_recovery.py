"""Bounded pre-battle recovery. Never retries a dispatched MAA battle."""
import time

import cv2

from .common import DATA, check_stop, log, now, write_json
from .campaign import stage_info
from .navigation import StageNotFound, is_settlement
from .motion import UnsettledFrameError
from .home_observation import navigation_regions, blocked_overlay
from . import workflow


def locate(nav, code, mode='normal'):
    """One normal search and one verified chapter re-entry, with fresh frames.

    Unknown pages, modal dialogs, stopped tasks and input/connection failures
    never become permission to navigate. Only a failed read-only arrival or
    exhausted map search is eligible. No ledger or battle state is touched.
    """
    prefix=stage_info(code,mode).get('group',code.rsplit('-',1)[0])
    trace=[]
    path=DATA/'navigation-recovery'/f'{time.time_ns()}-{code}-{mode}.json'
    for attempt in range(2):
        # find() owns the initial stop/input gates; check again before retrying.
        if attempt:check_stop()
        try:
            row=nav.find(code,mode=mode)
            if trace:
                write_json(path,dict(at=now(),code=code,mode=mode,status='recovered',
                                     observations=trace,result=row))
                log(f'{code} 自动重新定位成功，已确认关卡与难度')
            return row
        except (StageNotFound,UnsettledFrameError) as error:
            trace.append(dict(at=now(),attempt=attempt+1,error=str(error)))
            write_json(path,dict(at=now(),code=code,mode=mode,status='retry_pending' if attempt==0 else 'failed',observations=trace))
            if attempt:raise
            check_stop();nav.d.invalidate_frame()
            # observe() enforces motion and overlay gates; never blindly press
            # Back from a failed capture or an unrecognised page.
            try:
                image,texts=nav.observe()
                page,_=navigation_regions(texts,image)
                reason=blocked_overlay(texts)
                if reason or is_settlement(texts) or page not in ('map','sn_map','home','terminal','stories','is_overview','cw_morse'):
                    raise RuntimeError(reason or '自动定位恢复未确认可导航页面，保留现场') from error
                path.parent.mkdir(parents=True,exist_ok=True)
                png=path.with_suffix('.png')
                cv2.imencode('.png',image)[1].tofile(str(png))
                trace[-1].update(page=page,screenshot=str(png),texts=texts)
                write_json(path,dict(at=now(),code=code,mode=mode,status='reentering',observations=trace))
                workflow.update('navigation',chapter=prefix,code=code,mode=mode,
                                message=f'{code} 定位未完成，程序重新进入章节后再定位一次')
                log(f'{code} 定位未完成，自动重新进入 {prefix}（最多一次；尚未开战）')
                check_stop();nav.enter(prefix)
            except BaseException as recovery_error:
                write_json(path,dict(at=now(),code=code,mode=mode,status='stopped' if isinstance(recovery_error,InterruptedError) else 'failed',
                                     observations=trace,error=str(recovery_error)))
                raise
