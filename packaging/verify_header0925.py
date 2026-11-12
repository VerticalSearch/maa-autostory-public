"""Prove the short-ID header fallback reads the saved chapter-0 and chapter-17 frames.

0.9.24 and earlier never confirmed 第 0/1 章 raid details: the red raid banner plus
the 210 threshold dropped the leading digit of "0-5". This script re-reads the
saved frames with the shipped preprocessing and asserts both that the expected id
is read and that a neighbouring id is refused.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from autostory.device import Vision
from autostory.navigation import header_matches
from autostory.stage_header import contrast_header

CASES = (
    ('runtime/screens/inspect-0-5.png', 'raid', '0-5', '0-6'),
    ('runtime/screens/0-5-detail.png', 'normal', '0-5', '0-6'),
    ('runtime/screens/17-11-detail.png', 'normal', '17-11', '17-1'),
)


def main():
    vision = Vision()
    results = []
    for relative, mode, code, wrong in CASES:
        path = ROOT / relative
        if not path.exists():
            results.append({'frame': relative, 'ok': False, 'error': 'frame not retained'})
            continue
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        tokens = contrast_header(vision, image, code, mode=mode)
        texts = [t['text'] for t in tokens]
        results.append({'frame': relative, 'mode': mode, 'expect': code, 'tokens': texts,
                        'matched': header_matches(tokens, code),
                        'rejects_neighbour': not header_matches(tokens, wrong),
                        'neighbour': wrong})
    report = {'ok': all(r.get('matched') and r.get('rejects_neighbour') for r in results),
              'ocr': {'requested': vision.backend.get('requested'), 'active': vision.backend.get('active')},
              'cases': results}
    (ROOT / 'runtime/header0925-check.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
