"""Read short white stage IDs independently of the smaller OPERATION caption."""
import cv2
import re


def contrast_header(vision,image,code,mode='normal'):
    # This fallback is limited to the verified short-ID layout. Longer side-
    # story IDs retain their normal header ROI; a title alone is never enough.
    # IS EX/S uses the same complete 98px code strip. Its red banner can merge
    # the smaller caption into OFERATIONIS-EX-1; retained real raid pixels verify
    # this bounded extension. Do not extend it to longer/unknown side-story IDs.
    is_city_code=bool(re.fullmatch(r'IS-(?:EX-[1-8]|S-[1-5])',code))
    if (len(code)>5 and not is_city_code) or code.startswith('SN-'):return []
    if mode not in ('normal','raid'):return []
    # The raid banner shifts the whole code left. Do not try both positions:
    # a crop ending halfway through a normal ID could invent a shorter ID.
    left=875 if mode=='raid' else 915
    crop=image[77:113,left:left+98]
    if crop.size==0 or float(crop.std())<8:return []
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    # 210 kept only the brightest stroke cores: on the red raid banner the
    # leading digit of "0-5" disappeared and the header never matched. 190 with
    # one more upscale reads 0-5 and 17-11 on the saved chapter-0/17 screenshots.
    mask=cv2.threshold(gray,190,255,cv2.THRESH_BINARY_INV)[1]
    padded=cv2.copyMakeBorder(mask,14,14,14,14,cv2.BORDER_CONSTANT,value=255)
    # Nearest-neighbour keeps the binary strokes crisp; the default bilinear
    # upscale washed out the thin digit that the mask had just recovered.
    scaled=cv2.resize(padded,None,fx=4,fy=4,interpolation=cv2.INTER_NEAREST)
    image=cv2.cvtColor(scaled,cv2.COLOR_GRAY2BGR)
    return [t for t in vision.texts(image) if t.get('score',0)>=.85]
