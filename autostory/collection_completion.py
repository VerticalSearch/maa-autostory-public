"""Read activity completion on the collection list before opening any card."""
from __future__ import annotations

import hashlib
import time

import cv2
import numpy as np

from . import common, collection_index, collection_ocr
from .activity_completion import _label


def tab_target(texts):
    anchors = (('默认进度', (685, 15, 795, 68)),
               ('奖励进度', (798, 15, 905, 68)),
               ('完成进度', (908, 15, 1015, 68)))
    matches = [[t for t in texts if _label(t, label, bounds)] for label, bounds in anchors]
    if any(len(items) != 1 for items in matches):
        return None
    return matches[-1][0]


def selected(image, texts):
    if (not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3)
            or image.dtype != np.uint8 or tab_target(texts) is None):
        return False
    pixels = image[55:59, 921:999].astype(np.int16)
    blue, green, red = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    return float(((blue > 110) & (green > 90) & (blue-red > 65) & (green-red > 50)).mean()) > .35


def _badge(image, box):
    # A completed card's cyan icon is dimmed and carries a white tick.
    x1, y1, _, y2 = box
    x = int(x1)-42; y = int((y1+y2)/2)-20
    if x < 0 or y < 70 or x+40 > 1280 or y+40 > 720:
        return False
    pixels = image[y:y+40, x:x+40].astype(np.int16)
    b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    return float(((b >= 65) & (g >= 55) & (b-r >= 30) & (g-r >= 25)).mean()) >= .08


def inspect_page(image, texts, profiles, vision):
    """Two spatially bound OCR passes; no input or filesystem access."""
    if not selected(image, texts):
        return {}
    from .collection_progress import cards_from_rows
    def unique(rows):
        result = {}; duplicates = set()
        for card in cards_from_rows(rows, profiles):
            base = card['base_group']
            if base in result:duplicates.add(base)
            result[base] = card
        return {base: card for base, card in result.items() if base not in duplicates}
    first = unique(texts)
    second = unique(collection_ocr.columns(image, vision.texts_zoom, common.check_stop))
    result = {}
    for base, a in first.items():
        b = second.get(base)
        if b is None or any(a[k] != b[k] for k in ('title', 'current', 'total', 'full')):
            continue
        if any(abs(a['counter_box'][i]-b['counter_box'][i]) > 12 for i in range(4)):
            continue
        if not _badge(image, a['counter_box']):
            continue
        result[base] = {'base_group': base, 'title': a['title'], 'screen_kind': 'collection',
                        'completion_tab': True, 'current': a['current'], 'total': a['total'],
                        'score': min(a['score'], b['score']), 'probe_count': 2,
                        'counter_box': a['counter_box'], 'title_box': a['title_box']}
    return result


def _signature(image):
    return cv2.resize(image[90:690, 40:1160], (112, 60))


def _same(a, b):
    return a is not None and np.mean(cv2.absdiff(a, b)) < 2.5


def _require_selected(image, texts):
    if not selected(image, texts):
        raise RuntimeError('活动完成进度列表未确认，未读取或点击活动卡片')


def observe_selected(nav, observation=None):
    """Re-read only the header, then at most one fresh frame; never replay input."""
    rejected = []
    for attempt in range(2):
        common.check_stop()
        image, texts = observation if attempt == 0 and observation is not None else nav.observe()
        if selected(image, texts):
            return image, texts
        header = nav.v.texts_zoom(image, (680, 15, 345, 55), scale=2)
        if not isinstance(header, list):header = []
        if selected(image, header):
            # Do not duplicate full-frame header tokens in the rechecked result.
            return image, [t for t in texts if t.get('y', 0) >= 75] + header
        rejected.append((image, texts, header))
        if attempt == 0:
            nav.d.invalidate_frame()
            common.log('活动列表顶部标记未确认，重新读取一次静止画面')
    folder = common.DATA/'screens'/'activity-progress-rejected'/str(time.time_ns())
    folder.mkdir(parents=True, exist_ok=False)
    for n, (image, texts, header) in enumerate(rejected, 1):
        ok, encoded = cv2.imencode('.png', image)
        if ok:(folder/f'{n}.png').write_bytes(encoded.tobytes())
        common.write_json(folder/f'{n}.json', {'texts': texts, 'header': header})
    raise RuntimeError('活动完成进度列表未确认，未读取或点击活动卡片；证据：'+str(folder))


def _proof(base, readings, frames):
    folder = common.DATA/'screens'/'activity-progress'/f'{time.time_ns()}-{base}'
    folder.mkdir(parents=True, exist_ok=False)
    refs = []
    for n, image in enumerate(frames, 1):
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise RuntimeError('活动进度证据保存失败，未采用该进度')
        path = folder/f'{n}.png'; raw = encoded.tobytes(); path.write_bytes(raw)
        refs.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
    proof = {'schema': 1, 'source': 'activity_counter', 'base_group': base,
             'current': readings[0]['current'], 'total': readings[0]['total'],
             'at': common.now(), 'new_clear': False, 'observations': readings, 'frames': refs,
             'evidence': str(folder/'proof.json'),
             'run_id': common.read_json(common.DATA/'status.json', {}).get('run_id')}
    common.write_json(folder/'proof.json', proof)
    return proof


def collect(nav, profiles, max_pages=24):
    """A bounded fresh list walk. Full cards never need to be entered."""
    common.check_stop()
    nav._completed_activities = {}
    image, texts = nav.observe()
    if tab_target(texts) is None:
        nav.stories(); image, texts = nav.observe()
        if not nav.click_text('乐章收录', texts, exact=True):
            raise RuntimeError('统一章节列表入口未确认')
        image, texts = nav.observe()
    target = tab_target(texts)
    if target is None:
        raise RuntimeError('乐章收录进度选项未确认')
    if not selected(image, texts):
        # One confirmed tab click, followed by positive selection verification.
        nav.d.tap(target['x'], target['y'], 1.0)
        image, texts = nav.observe()
    image, texts = observe_selected(nav, (image, texts))
    limit = max(1, min(int(max_pages), collection_index.MAX_PAGES))
    # The user may already be midway through the list. Reach the top with
    # bounded, checked scrolling before assigning page offsets.
    previous = None; stationary = 0; top_confirmed = False
    for _ in range(limit):
        common.check_stop(); image, texts = observe_selected(nav)
        sig = _signature(image)
        stationary = stationary+1 if _same(previous, sig) else 0; previous = sig
        if stationary >= 2:
            top_confirmed = True; break
        nav.d.swipe(1150, 290, 1150, 590, duration=1000)
    if not top_confirmed:
        raise RuntimeError('活动完成进度列表顶端未确认，未生成待推图名单')
    progress = {}; conflicts = set(); pages = {}; previous = None; stationary = 0
    end_confirmed = False
    for page in range(limit):
        common.check_stop(); image, texts = nav.observe(f'activity-progress-list-{page}')
        image, texts = observe_selected(nav, (image, texts))
        first = inspect_page(image, texts, profiles, nav.v)
        if first:
            nav.d.invalidate_frame(); common.check_stop()
            again, again_texts = observe_selected(nav)
            second = inspect_page(again, again_texts, profiles, nav.v)
            for base, reading in first.items():
                recheck = second.get(base)
                if recheck is None or any(reading[k] != recheck[k] for k in ('current', 'total', 'title')):
                    progress.pop(base, None); conflicts.add(base); continue
                old = progress.get(base)
                if old and any(old[k] != reading[k] for k in ('current', 'total')):
                    progress.pop(base, None); conflicts.add(base); continue
                if base in conflicts:
                    continue
                pages.setdefault(collection_index.key(reading['title']), page)
                if old is None:
                    progress[base] = _proof(base, [reading, recheck], [image, again])
                    common.log(f'活动列表进度：{reading["title"]} {reading["current"]}/{reading["total"]}'
                               + ('，跳过整个活动' if reading['current'] == reading['total'] else '，列入待检查活动'))
        sig = _signature(image)
        stationary = stationary+1 if _same(previous, sig) else 0; previous = sig
        if stationary >= 2:
            end_confirmed = True; break
        nav.d.swipe(1150, 590, 1150, 290, duration=1000)
    nav._completed_activities = {base: p for base, p in progress.items() if p['current'] == p['total']}
    # Advisory locations only. Completion evidence is deliberately never loaded
    # from an old disk cache for a new run/account.
    nav.collection_index = collection_index.save(pages, page+1, complete=end_confirmed)
    common.write_json(common.DATA/'activity-progress.json', {
        'at': common.now(), 'run_id': common.read_json(common.DATA/'status.json', {}).get('run_id'),
        'progress': progress, 'conflicts': sorted(conflicts), 'list_end_confirmed': end_confirmed,
        'pages': page+1, 'new_clear': False})
    return progress
