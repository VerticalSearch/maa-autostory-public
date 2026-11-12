"""Verified same-story zone transitions; no guessed map coordinates."""
import re
import cv2
import numpy as np
from .common import ROOT,check_stop,log


def fold(text):
    return re.sub(r'[^A-Z0-9\u4e00-\u9fff]','',str(text).upper())


def map_groups(texts,profiles):
    from .navigation import detail_mode,is_settlement
    from .home_observation import blocked_overlay
    if detail_mode(texts) or is_settlement(texts) or blocked_overlay(texts):return set()
    codes={str(t.get('text','')).replace(' ','').upper() for t in texts
           if t.get('score',0)>=.9 and 5<t.get('x',0)<1275 and 90<t.get('y',0)<610}
    groups={name for name,profile in profiles.items() if codes.intersection(profile.get('codes',()))}
    # Two different exact operation codes prove a map, independent of captions.
    known=set().union(*(set(profiles[name].get('codes',())) for name in groups)) if groups else set()
    return groups if len(codes.intersection(known))>=2 else set()


def same_zone(target,observed,profiles):
    wanted=profiles.get(target,{})
    zones=set(wanted.get('zones',()))
    return bool(zones) and any(profiles[name].get('base',name)==wanted.get('base',target)
        and zones==set(profiles[name].get('zones',())) for name in observed)


def text_button(texts,prefix,profile):
    labels={fold(label) for label in profile.get('zones',())}
    labels|={'EX'+label for label in tuple(labels)}
    # Exact English captions are independent evidence for the decorated PV tabs.
    labels.update({'PV-EX':('SPLENDIDACAVALCATA',),'PV-S':('DECADANZA',)}.get(prefix,()))
    candidates=[]
    for t in texts:
        if (t.get('score',0)<.9 or not 570<t.get('y',0)<710 or
                not 10<t.get('x',0)<1265 or fold(t.get('text','')) not in labels):continue
        if any(abs(t['x']-p['x'])<55 and abs(t['y']-p['y'])<45 for p in candidates):continue
        candidates.append(t)
    return candidates[0] if len(candidates)==1 else None


def icon_button(image,prefix,observed):
    if prefix in ('NL-EX','NL-S','MN-EX','DV-EX'):
        from .zone_icons import match_zone_icon
        return match_zone_icon(image,prefix,observed,ROOT/'assets')
    if prefix!='HE-EX' or not observed.intersection(('HE','HE-TR')):return None
    path=ROOT/'assets/zone-he-ex.png'
    if not path.exists():return None
    template=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_GRAYSCALE)
    if template is None:return None
    roi=image[580:715,1060:1240]
    if roi.shape[0]<template.shape[0] or roi.shape[1]<template.shape[1]:return None
    scores=cv2.matchTemplate(cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY),template,cv2.TM_CCOEFF_NORMED)
    _,score,_,point=cv2.minMaxLoc(scores)
    if not np.isfinite(score) or score<.92:return None
    return dict(x=1060+point[0]+template.shape[1]/2,
                y=580+point[1]+template.shape[0]/2,text='HE EX 图标',score=score)


def enter_zone(nav,prefix,profiles,*,reuse=False):
    """Return True only for the requested map/known shared zone.

    Used after base entry, or for an already confirmed same-story context.
    Every actual tap is followed by fresh map-code verification.
    """
    base=profiles[prefix].get('base',prefix)
    image,texts=nav.observe('zone-'+prefix)
    observed=map_groups(texts,profiles)
    from .cw_morse import is_map
    if is_map(image,texts):observed={'CW-S'}
    if not observed or any(profiles[g].get('base',g)!=base for g in observed):return False
    if prefix in observed or same_zone(prefix,observed,profiles):
        if reuse:log(f'复用当前 {base} 地图，继续 {prefix}')
        return True
    button=text_button(texts,prefix,profiles[prefix])
    if button is None:
        extra=nav.v.texts_zoom(image,(0,570,1280,140),scale=2)
        button=text_button(extra,prefix,profiles[prefix])
    if button is None:button=icon_button(image,prefix,observed)
    if button is None:return False
    check_stop()
    nav.d.tap(button['x'],button['y'],.3)
    # Do not replay a click when a page is slow; await its postcondition instead.
    nav.await_state(lambda im,t: (prefix=='CW-S' and is_map(im,t)) or prefix in map_groups(t,profiles)
                    or same_zone(prefix,map_groups(t,profiles),profiles),prefix+' 分区切换')
    log(f'已确认 {base} 分区切换：{prefix}')
    return True
