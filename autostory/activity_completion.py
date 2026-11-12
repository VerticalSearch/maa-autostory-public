"""Recognize a full activity counter without issuing any game input."""
from __future__ import annotations

import math
import re

import numpy as np

from .activity_counter import ratio_from_rows


class ActivityAlreadyComplete(RuntimeError):
    def __init__(self, proof):
        self.proof = proof
        self.base = proof['base_group']
        super().__init__(f'{self.base} 活动总进度 {proof["current"]}/{proof["total"]} 已满，跳过整个活动')


def activity_base(prefix, profiles):
    profile = profiles.get(prefix, {})
    if not isinstance(profile, dict) or profile.get('kind') != 'side':
        return None
    base = profile.get('base', prefix)
    owner = profiles.get(base, {})
    if not isinstance(owner, dict) or owner.get('kind') != 'side':
        return None
    return base


def _label(row, value, bounds, minimum=.9):
    try:
        box = row['box']
        return (re.sub(r'\s+', '', row['text']) == value
                and type(row['score']) in (int, float) and math.isfinite(row['score'])
                and row['score'] >= minimum and len(box) == 4
                and bounds[0] <= box[0] < box[2] <= bounds[2]
                and bounds[1] <= box[1] < box[3] <= bounds[3])
    except (KeyError, TypeError, ValueError):
        return False


def _cyan_badge(image, roi):
    x, y, w, h = roi
    pixels = image[y:y+h, x:x+w].astype(np.int16)
    blue, green, red = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    mask = (blue >= 100) & (green >= 95) & (blue-red >= 45) & (green-red >= 35)
    return float(mask.mean()) >= .12


def _identity(image, texts, base, profiles):
    # Only the currently selected large title can identify the intro. A nearby
    # carousel logo is not enough, and the top-right 3/3 resource is never read.
    title = profiles[base].get('title', '')
    if (title and any(_label(t, title, (850, 45, 1265, 290)) for t in texts)
            and any(_label(t, '进入活动', (1000, 565, 1265, 680)) for t in texts)
            and any(_label(t, '本章回想', (45, 75, 220, 180)) for t in texts)
            and _cyan_badge(image, (748, 590, 53, 54))):
        return 'intro', (798, 591, 120, 58), (-720, 40)
    words = ''.join(str(t.get('text', '')).replace(' ', '') for t in texts)
    if any(word in words for word in ('开始行动', '开始作战', '开始突袭', '行动结束',
            '演习结束', '行动失败', '演习失败', '放弃行动', 'MISSIONRESULTS')):
        return None
    codes = set()
    for group, profile in profiles.items():
        if activity_base(group, profiles) == base:
            codes.update(profile.get('codes', []))
    found = {code for code in codes
             if any(_label(t, code, (5, 90, 1275, 610)) for t in texts)}
    # Story nodes are deliberately absent from the battle catalogue. At the
    # end of a map (for example FC-8 / FC-ST-2 / FC-ST-3), they can corroborate
    # one catalogued battle node without ever becoming battle targets. Require
    # that anchor; arbitrary same-prefix text or two story labels are not enough.
    story_pattern = re.compile(re.escape(base) + r'-ST-?[1-9][0-9]{0,2}')
    story_nodes = {re.sub(r'\s+', '', str(t.get('text', ''))) for t in texts
                   if isinstance(t, dict)}
    story_nodes = {code for code in story_nodes if story_pattern.fullmatch(code)
                   and any(_label(t, code, (5, 90, 1275, 610)) for t in texts)}
    if found and len(found | story_nodes) >= 2 and _cyan_badge(image, (14, 635, 64, 69)):
        return 'map', (78, 635, 115, 62), (0, 0)
    return None


def inspect_full_counter(image, texts, prefix, profiles, vision):
    """Return one supported-frame observation, or None. No files/device I/O."""
    base = activity_base(prefix, profiles)
    if (base is None or not isinstance(image, np.ndarray)
            or image.shape != (720, 1280, 3) or image.dtype != np.uint8
            or not isinstance(texts, list)):
        return None
    identity = _identity(image, texts, base, profiles)
    if identity is None:
        return None
    kind, roi, offset = identity
    results = []
    for scale in (1, 2):
        rows = vision.texts_zoom(image, roi, scale=scale)
        shifted = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('box'), (list, tuple)) or len(row['box']) != 4:
                continue
            x1, y1, x2, y2 = row['box']
            if not (roi[0] <= x1 < x2 <= roi[0]+roi[2]
                    and roi[1] <= y1 < y2 <= roi[1]+roi[3]):
                continue
            dx, dy = offset
            shifted.append({**row, 'box': [x1+dx, y1+dy, x2+dx, y2+dy],
                            'x': (x1+x2)/2+dx, 'y': (y1+y2)/2+dy})
        value = ratio_from_rows(shifted)
        if not value or not value['full']:
            return None
        results.append(value)
    if len({(v['current'], v['total']) for v in results}) != 1:
        return None
    return {'base_group': base, 'screen_kind': kind, 'current': results[0]['current'],
            'total': results[0]['total'], 'score': min(v['score'] for v in results),
            'probe_count': 2}
