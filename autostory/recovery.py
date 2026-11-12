"""Bounded recovery after an evidenced practice loss; never abandons a fight."""
import time
from .common import DATA, check_stop, now, write_json, DeviceUnavailableError


class BattleFailure(RuntimeError):
    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence=str(evidence) if evidence else None


class PracticeFailure(BattleFailure):pass


class FormalFailure(BattleFailure):
    """A confirmed paid-battle loss must stop the run, never auto-retry it."""


class RecoveryFailure(RuntimeError):pass


class PracticeBudgetExhausted(RuntimeError):pass


class StateIntegrityError(RuntimeError):pass


def failure_screen(texts):
    words=''.join(str(x.get('text','')).replace(' ','').upper() for x in texts)
    return any(token in words for token in ('任务失败','行动失败','演习失败','MISSIONFAILED'))


def recover_practice(nav, code, mode, prefix, attempts=48, timeout=30):
    """Only acknowledge a known loss with its explicit continue prompt.

    The caller must have verified exercise start and a failed result, or a
    specific same-task exercise-only execution abort. Unknown/loading frames
    are only observed; settlement clicks require the exact target header.
    Abort recovery never proves a win.
    Returning requires an exact target detail/mode check via Navigator.find.
    """
    evidence=DATA/'screens'/'recovery'/f'{time.time_ns()}-{code}-{mode}'
    from .navigation import header_matches,is_settlement
    from .motion import UnsettledFrameError
    clicked=0;settlement_clicks=0
    deadline=time.monotonic()+timeout
    try:
        for index in range(attempts):
            check_stop()
            if time.monotonic()>=deadline:break
            try:
                _,texts=nav.observe(str(evidence/f'{index:02d}'))
            except UnsettledFrameError:
                # Loading may outlast one navigation settling window. Wait for
                # another fresh observation; do not replay the continue click.
                write_json(evidence/f'{index:02d}.json',{'at':now(),'words':'','loading':True})
                remaining=deadline-time.monotonic()
                if remaining>0:time.sleep(min(.5,remaining))
                continue
            words=' '.join(str(x.get('text','')) for x in texts)
            write_json(evidence/f'{index:02d}.json',{'at':now(),'words':words})
            if any(x.get('text') in ('放弃行动','继续行动') for x in texts):
                raise RecoveryFailure('画面可能仍在战斗，未执行自动恢复')
            if failure_screen(texts):
                if clicked>=2:
                    raise RecoveryFailure('失败结算页未退出，停止更换作业')
                if not nav.click_text('点击画面以继续',texts,exact=True):
                    raise RecoveryFailure('未确认失败结算的继续按钮，停止更换作业')
                clicked+=1
                continue
            if is_settlement(texts):
                if not header_matches(texts,code):
                    raise RecoveryFailure('失败后的结算关卡编号未确认，停止更换作业')
                if settlement_clicks>=2:
                    raise RecoveryFailure('失败后的行动结算页未退出，停止更换作业')
                # A failed exercise also has an operation-result/EXP page.
                # It is only acknowledged after caller-proved exercise loss,
                # exact stage header verification, and a bounded click count.
                nav.d.tap(1255,180,.8)
                settlement_clicks+=1
                continue
            if any(x.get('text') in ('开始行动','开始作战','开始突袭') for x in texts) or nav.visible_stages(texts,prefix):
                row=nav.find(code,mode=mode)
                if row.get('code')!=code or row.get('mode')!=mode or row.get('state') not in ('cleared','uncleared','partial'):
                    raise RecoveryFailure('恢复后未确认目标关卡与难度')
                record={'at':now(),'code':code,'mode':mode,'result':'recovered',
                        'continue_clicks':clicked,'settlement_clicks':settlement_clicks,'evidence':str(evidence)}
                write_json(evidence/'result.json',record)
                return record
            # Wait out transitions; no corner/back presses on unknown frames.
            remaining=deadline-time.monotonic()
            if index+1<attempts and remaining>0:time.sleep(min(.5,remaining))
        raise RecoveryFailure('演习失败后未返回可确认地图，停止更换作业')
    except (InterruptedError,DeviceUnavailableError):raise
    except Exception as exc:
        write_json(evidence/'result.json',{'at':now(),'code':code,'mode':mode,
                   'result':'blocked','reason':str(exc),'continue_clicks':clicked,'settlement_clicks':settlement_clicks})
        if isinstance(exc,RecoveryFailure):raise
        raise RecoveryFailure('演习恢复失败：'+str(exc)) from exc
