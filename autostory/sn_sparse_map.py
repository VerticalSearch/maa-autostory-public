"""Motion anchors for an evidenced SN viewport with only one battle node.

The two fixed page controls identify the map, but are NOT motion anchors.
Both selected motion anchors are attached to the moving node: its exact code
and an independently matched white crossed-tools glyph. No guessed code or
click coordinate is returned. Unknown/overlay/blank/other-region pages fail.
"""
from functools import lru_cache
import cv2
import numpy as np
from .common import ROOT
from . import sn_observation as sn

@lru_cache(maxsize=1)
def _template():
    path=ROOT/'assets/sn-node-tools.png'
    if not path.is_file():return None
    return cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_GRAYSCALE)

def regions(image,texts,known_codes):
    if not sn._valid_image(image) or sn._overlay_seen(texts):return ()
    if sn._anchor(texts,sn.FREE_EXPLORE,sn.FREE_EXPLORE_BOUNDS) is None:return ()
    if sn._anchor(texts,sn.CURRENT_REGION,sn.CURRENT_REGION_BOUNDS) is None:return ()
    nodes=sn._resolve(sn._detections(texts,sn._known(known_codes)))
    if len(nodes)!=1:return ()
    node=next(iter(nodes.values()))
    x1,y1,x2,y2=node.entry['box'];cx=(x1+x2)/2
    # Search only above this exact node, never another part of the map.
    left,top=int(cx)-37,int(y1)-77
    if left<5 or top<90 or left+74>1274:return ()
    template=_template()
    if template is None:return ()
    crop=cv2.cvtColor(image[top:top+56,left:left+74],cv2.COLOR_BGR2GRAY)
    if crop.shape[0]<template.shape[0] or crop.shape[1]<template.shape[1]:return ()
    score,point=cv2.minMaxLoc(cv2.matchTemplate(crop,template,cv2.TM_CCOEFF_NORMED))[1::2]
    if score<.95:return ()
    dx,dy=point;h,w=template.shape
    return ((int(x1)-1,int(y1)-1,int(x2-x1)+2,int(y2-y1)+2),
            (left+dx,top+dy,w,h))
