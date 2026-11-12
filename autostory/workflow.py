"""Small, run-scoped UI progress; battle evidence remains in its own records."""
import threading
from .common import DATA, now, write_json

_lock = threading.RLock()
_state = {}
LABELS = {'starting':'正在启动后台任务', 'refresh':'正在更新干员快照',
          'navigation':'正在进入关卡地图', 'scan':'正在识别普通与突袭进度',
          'planning':'正在匹配已有干员与作业', 'practice':'正在演习验证',
          'training':'正在补齐作业所需技能，缺料时按需合成',
          'formal':'演习已通过，正在正式作战', 'verification':'正在核验结算与通关星级',
          'finished':'本轮任务已完成', 'partial':'本轮部分完成，进度已保存',
          'budget_exhausted':'本轮预算已用完，进度已保存', 'stopped':'任务已停止，进度已保存', 'error':'任务已暂停'}

def begin(run_id, chapter=''):
    with _lock:
        _state.clear()
        _state.update(run_id=run_id, chapter=chapter, code='', mode='normal')
        update('starting')

def update(phase=None, **fields):
    with _lock:
        # Imports and offline tests must not create a live workflow record.
        if not _state.get('run_id'):return
        if phase is not None:
            _state.update(phase=phase, message=LABELS.get(phase,phase))
        _state.update(fields, updated=now())
        write_json(DATA/'workflow.json',_state)

def end(phase, message=None):
    with _lock:
        update(phase, **({'message':message} if message else {}))
        _state.clear()
