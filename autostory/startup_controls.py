"""Select only the two evidenced startup controls, never account/QR entries."""
import math


def _unique(texts,label,bounds):
    left,top,right,bottom=bounds
    rows=[]
    for row in texts:
        if str(row.get('text','')).replace(' ','').upper()!=label:continue
        values=[row.get(key) for key in ('score','x','y')]
        if not all(isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) for value in values):continue
        score,x,y=values
        if .9<=score<=1 and left<x<right and top<y<bottom:rows.append(row)
    return rows[0] if len(rows)==1 else None


def start_button(image,texts,vision):
    """Caller must first classify the exact foreground as login_start.

    A second fresh call immediately before tapping is mandatory. Resource
    START uses both three page anchors and the high-confidence local template;
    the cached-account title uses exact brand and wake-up OCR coordinates.
    """
    wake=_unique(texts,'开始唤醒',(520,440,760,550))
    brand=_unique(texts,'ARKNIGHTS',(430,320,850,460))
    if wake and brand:return {key:wake[key] for key in ('text','score','x','y')}
    clear=_unique(texts,'清除缓存',(15,10,160,75))
    network=_unique(texts,'网络检测',(165,10,330,75))
    versions=[row for row in texts if str(row.get('text','')).upper().startswith('VER')
              and isinstance(row.get('score'),(int,float)) and .8<=row['score']<=1
              and isinstance(row.get('x'),(int,float)) and 15<row['x']<300
              and isinstance(row.get('y'),(int,float)) and 60<row['y']<100]
    if not (clear and network and len(versions)==1):return None
    match=vision.match(image,'GameStart',roi=(550,600,200,120),threshold=.94)
    if match and match.get('score',0)>=.94 and 575<match.get('x',0)<705 and 640<match.get('y',0)<710:
        return dict(text='资源页开始',**match)
    return None
