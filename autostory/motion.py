"""Bounded visual settling. Stability is never evidence of a game state."""
from __future__ import annotations
import time
import cv2
import numpy as np


class UnsettledFrameError(RuntimeError):
    """Fresh frames did not settle; callers may re-observe, never replay input."""


def signature(image, roi=None):
    if image is None or image.size == 0:
        raise ValueError('Empty frame')
    if roi is not None:
        x,y,w,h=map(int,roi)
        if w<=0 or h<=0 or x<0 or y<0 or x+w>image.shape[1] or y+h>image.shape[0]:
            raise ValueError('Invalid stability ROI')
        image=image[y:y+h,x:x+w]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Tiny OCR boxes are enlarged below. Prefilter their anti-aliased glyph
    # edges at native scale so subpixel rendering shimmer is not magnified.
    # Large state ROIs and the full-frame loading guard remain unchanged.
    if roi is not None and 20<=gray.shape[0]<=80 and 30<=gray.shape[1]<=180:
        gray=cv2.GaussianBlur(gray,(7,7),0)
    return cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)


def frame_motion(previous, current):
    """Track both global change and moving regions; ignore tiny idle effects."""
    delta = cv2.absdiff(previous, current)
    mean = float(delta.mean())
    changed = float(np.mean(delta > 16))
    # Flat loading/black frames must never satisfy the settling condition.
    textured = float(current.std()) >= 8 and float(np.mean(current > 25)) >= .03
    return {'mean_delta': mean, 'changed_fraction': changed,
            'textured': textured, 'stable': textured and mean <= 2.0 and changed <= .015}


def map_signature(image,roi):
    """Native-resolution map labels; subpixel motion must not be magnified."""
    x,y,w,h=map(int,roi)
    if w<=0 or h<=0 or x<0 or y<0 or x+w>image.shape[1] or y+h>image.shape[0]:raise ValueError('Invalid map ROI')
    return cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY).astype(np.float32)


def map_motion(previous,current):
    """Bounded subpixel drift for already identified map labels only.

    Map16 labels drift even without input. Two known labels, a short anchored
    window, high structural correlation and fresh final OCR are required by
    the caller. This gate is never used for detail/start/settlement controls.
    """
    a=previous.astype(np.float32);b=current.astype(np.float32)
    textured=min(float(a.std()),float(b.std()))>=8
    if not textured:return {'stable':False,'textured':False}
    correlation=float(np.corrcoef(a.ravel(),b.ravel())[0,1])
    h,w=a.shape;ph=1<<(h-1).bit_length();pw=1<<(w-1).bit_length()
    # Even DFT extents avoid phaseCorrelate's half-pixel centre bias when the
    # internally padded transform has an odd optimal size.
    a=cv2.copyMakeBorder(a,0,ph-h,0,pw-w,cv2.BORDER_CONSTANT)
    b=cv2.copyMakeBorder(b,0,ph-h,0,pw-w,cv2.BORDER_CONSTANT)
    shift,response=cv2.phaseCorrelate(a,b,cv2.createHanningWindow((pw,ph),cv2.CV_32F))
    dx,dy=map(float,shift)
    stable=bool(np.isfinite([correlation,response,dx,dy]).all() and correlation>=.92
                and response>=.8 and max(abs(dx),abs(dy))<=.85)
    return {'stable':stable,'textured':textured,'correlation':correlation,
            'translation':[dx,dy],'response':float(response)}


TEXTURE_MIN_STD = 8.0


def region_texture(image, roi):
    """Standard deviation of one anchor box at native scale.

    ``map_motion`` refuses boxes below this texture, so a box that carries no
    label pixels can never satisfy a map settle. Callers use this to reject
    such boxes before committing to the map gate.
    """
    shape=getattr(image,'shape',None)
    if not isinstance(shape,tuple) or len(shape)<2:
        return None
    height,width=shape[:2]
    try:
        x,y,w,h=map(int,roi)
    except (TypeError,ValueError):
        return 0.0
    if w<=0 or h<=0 or x<0 or y<0 or x+w>width or y+h>height:
        return 0.0
    crop=cv2.cvtColor(image[y:y+h,x:x+w],cv2.COLOR_BGR2GRAY)
    return float(crop.std())


def textured_regions(image, rois, minimum=TEXTURE_MIN_STD):
    """Keep only anchor boxes that still contain label pixels.

    A label box sampled from a panned or fading frame can land on blank
    pixels. Feeding it to the map gate makes ``settle`` unsatisfiable and burns
    the whole deadline before the caller re-selects. Frames that are not real
    images are returned unchanged so callers keep their previous behaviour.
    """
    if not hasattr(image,'shape') or not isinstance(getattr(image,'shape',None),tuple):
        return tuple(rois)
    return tuple(roi for roi in rois if (region_texture(image,roi) or 0.0)>=minimum)


def settle(capture, check_stop, wait, *, timeout=3.5, interval=.15,
           confirmations=2, clock=time.monotonic, rois=(),kind='fixed'):
    """Require consecutive stable comparisons. Never inject input while waiting."""
    if kind not in ('fixed','map'):raise ValueError('Unknown settling mode')
    if kind=='map' and len(rois)<2:raise ValueError('Map settling requires two known labels')
    compare=map_motion if kind=='map' else frame_motion
    region_signature=map_signature if kind=='map' else signature
    started = clock()
    previous = None
    anchors = None
    previous_regions = None
    consecutive = 0
    blank_streak = 0
    count = 0
    metrics = {}
    while True:
        check_stop()
        image = capture()
        check_stop()
        count += 1
        current = signature(image)
        regions=[region_signature(image,roi) for roi in rois] if rois else [current]
        if previous is not None:
            metrics = frame_motion(previous, current)
            local=[compare(a,b) for a,b in zip(previous_regions,regions)]
            drift=[compare(a,b) for a,b in zip(anchors,regions)]
            # The anchor prevents a succession of tiny moves passing as stationary.
            stable=metrics['textured'] and all(r['stable'] for r in local+drift)
            consecutive = consecutive + 1 if stable else 0
            if not stable:anchors=regions
            # An anchor box without pixels can never compare stable. Two such
            # comparisons mean the selection is wrong, not that the page moves:
            # fail fast so the caller re-selects instead of burning the deadline.
            blank_anchor=kind=='map' and any(r.get('textured') is False for r in local+drift)
            blank_streak=blank_streak+1 if blank_anchor else 0
            metrics={**metrics,'stable':stable,'regions':local if rois else [],
                     'anchor_stable':all(r['stable'] for r in drift),'roi_count':len(rois),
                     'blank_anchor':blank_anchor}
        else:anchors=regions
        elapsed = clock() - started
        report = {**metrics, 'kind':kind,'frames': count, 'elapsed_seconds': elapsed,
                  'blank_streak': blank_streak,
                  'confirmed': consecutive >= confirmations and elapsed <= timeout,
                  'rois': [list(roi) for roi in rois]}
        if report['confirmed'] or elapsed >= timeout:
            return image, report
        if blank_streak>=2:
            report.update(confirmed=False, reason='锚点框内没有可比较的标签像素，提前结束本轮静止检查')
            return image, report
        previous = current
        previous_regions = regions
        wait(min(interval, max(0, timeout - elapsed)))
