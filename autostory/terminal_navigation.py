"""Verify Terminal arrival and stop a round on a shared navigation failure."""
import time

from .common import check_stop, log
from .home_observation import blocked_overlay, home_regions, navigation_regions


class TerminalNavigationError(RuntimeError):
    """Home/Terminal is unavailable, so trying other chapters cannot help."""


def terminal_visible(texts):
    """Two independent, correctly located controls; a lone title is not arrival."""
    if blocked_overlay(texts) or home_regions(texts):
        return False
    if navigation_regions(texts)[0] in ('terminal', 'stories'):
        return True
    # Other Terminal tabs may not display the last-operation shortcut. Their
    # bottom navigation remains visible. Require two different exact labels.
    anchors = {
        '终端': (45, 630, 165, 710),
        '曲谱': (255, 620, 385, 715),
        '资源收集': (445, 620, 620, 715),
        '周期事务': (660, 620, 850, 715),
        '长期事务': (880, 620, 1070, 715),
    }
    from .home_observation import fixed_regions
    return sum(bool(fixed_regions(texts, ((label, bounds),)))
               for label, bounds in anchors.items()) >= 2


def _pause(seconds):
    deadline = time.monotonic() + seconds
    while True:
        check_stop()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(.1, remaining))


def _evidence(image, trace):
    """Keep the last observed frame and input decisions without recapturing."""
    from .common import DATA, now, write_json
    folder = DATA / 'screens' / 'terminal-failures'
    target = folder / str(time.time_ns())
    try:
        folder.mkdir(parents=True, exist_ok=True)
        screenshot = None
        if image is not None:
            import cv2
            encoded, pixels = cv2.imencode('.png', image)
            if encoded:
                screenshot = str(target.with_suffix('.png'))
                pixels.tofile(screenshot)
        write_json(target.with_suffix('.json'), {'at': now(), 'evidence': screenshot, 'observations': trace})
        return str(target.with_suffix('.json'))
    except Exception as error:
        log(f'终端失败证据保存失败：{error}')
        return None


def open_terminal(nav):
    # A home failure affects every chapter, not the selected chapter's support.
    try:
        nav.home()
    except RuntimeError as error:
        raise TerminalNavigationError(f'进入终端前无法确认主页：{error}；已保存断点，停止本轮') from error
    trace = []
    image = None
    taps = 0
    # Three clicks at most, always from a freshly verified home. Every click
    # has subsequent read-only observations, including the final click.
    for observation in range(8):
        check_stop()
        try:
            image, texts = nav.observe()
        except RuntimeError as error:
            trace.append({'observation': observation, 'error': str(error)})
            evidence = _evidence(image, trace)
            raise TerminalNavigationError(
                f'终端观察失败：{error}；已保存断点，停止本轮；诊断记录：{evidence or "未能保存"}') from error
        home = bool(home_regions(texts))
        arrived = terminal_visible(texts)
        entry = {'observation': observation, 'home': home, 'terminal': arrived, 'texts': texts}
        trace.append(entry)
        if arrived:
            return
        if blocked_overlay(texts):
            break
        buttons = [t for t in texts if t.get('text', '').replace(' ', '') == '终端'
                   and t.get('score', 0) >= .85
                   and 890 < t.get('x', 0) < 1045 and 110 < t.get('y', 0) < 230]
        if home and len(buttons) == 1 and taps < 3 and observation < 7:
            button = buttons[0]
            entry['tap'] = [button['x'], button['y']]
            taps += 1
            nav.d.tap(button['x'], button['y'], 3.0)
        else:
            _pause(.5)
    evidence = _evidence(image, trace)
    suffix = f'；诊断记录：{evidence}' if evidence else ''
    raise TerminalNavigationError(
        '终端页面未确认；已保存断点并停止本轮，避免后续章节重复失败。'
        '请待游戏可正常进入终端后继续'+suffix)


def terminal(self):
    # This small method is also overlaid on the frozen baseline Navigator.
    from .terminal_navigation import open_terminal
    return open_terminal(self)
