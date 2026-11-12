from __future__ import annotations
import re
import time
from difflib import SequenceMatcher
import cv2
import numpy as np
from .common import DATA, log, now, read_json, write_json, check_stop
from .jobs import Catalog
from .resilient_ocr import OCRBackendChanged
from .campaign import catalogue,stage_info

# A recognised title can still fail to open (inertia, a transient overlay).
# Three fresh verified observations is enough; more only risks repeated taps.
ENTRY_TAP_LIMIT=3


class MapResourcesUnavailable(RuntimeError):
    navigation_unchanged=True


class StageNotFound(RuntimeError):
    """A bounded map search ended before selecting or starting a battle."""

CHAPTERS={
 'DV':{'title':'绿野幻梦','line':'摘取未来之人'},
 'CW':{'title':'孤星','line':'摘取未来之人'},
 'SN':{'title':'愚人号','line':'自海渊'},
 'SV':{'title':'覆潮之下','line':'自海渊'},
 'BP':{'title':'生路','line':'自海渊'},
 'EP':{'title':'出苍白海','line':'自海渊'},
 'LE':{'title':'尘影余音','line':'高塔迷影'},
 'HE':{'title':'空想花庭','line':'高塔迷影'},
 'FC':{'title':'照我以火','line':'薪火重燃'},
}
for _prefix,_profile in catalogue()['profiles'].items():
    if _profile['kind']=='side':
        CHAPTERS[_prefix]={**_profile,**CHAPTERS.get(_prefix,{})}

def norm(s):return re.sub(r'\s+','',s).replace('—','-').replace('–','-')

def header_matches(texts,code):
    parts=[]
    for t in texts:
        value=norm(t['text']).upper().replace('OPERATION','').replace('OPERATON','')
        value=re.sub(r'[^A-Z0-9-]','',value)
        # "OPERATION-0-5" keeps the separator as a leading dash once the caption
        # is removed; the id itself never starts with one. The raw ``value``
        # still feeds the split-token merge below, which needs that dash.
        if value.strip('-')==code:return True
        if value.endswith(code):
            heading=value[:-len(code)]
            if heading.startswith('OP') and 6<=len(heading)<=10 and SequenceMatcher(None,heading,'OPERATION').ratio()>=.7:
                return True
        if value:parts.append((t['x'],t['y'],value))
    for _,y,_ in parts:
        line=re.sub(r'-+','-',''.join(p[2] for p in sorted(parts) if abs(p[1]-y)<9))
        if line==code:return True
    return False

def classify_stars(image,layout='standard'):
    """Only positive cyan medal evidence is classified. Zero cyan is NOT uncleared."""
    crop=image[608:678,78:282] if layout=='sn' else image[78:155,1008:1220]
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    mask=cv2.inRange(hsv,np.array([80,110,100]),np.array([112,255,255]))
    n,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
    parts=[]
    for x,y,w,h,area in stats[1:]:
        if 22<=w<=72 and 25<=h<=72 and area>=400:parts.append((int(x),int(y),int(w),int(h),int(area)))
    parts.sort()
    count=len(parts)
    if count==3 and all(35<=parts[i+1][0]-parts[i][0]<=80 for i in range(2)):
        return 'cleared',3,parts
    if count in (1,2):return 'partial',count,parts
    return 'unknown',None,parts

def detail_mode(texts):
    words={norm(x['text']) for x in texts}
    if '开始突袭' in words and words.intersection({'普通模式','普通难度'}):return 'raid'
    if words.intersection({'开始行动','开始作战'}):return 'normal'
    return None

def is_settlement(texts):
    words=''.join(norm(x['text']).upper() for x in texts)
    return any(s in words for s in ('行动结束','演习结束','MISSIONRESULTS','任务失败','行动失败','演习失败'))

def classify_raid(image,texts):
    # Raid completion is the fourth white medal, not the three blue normal medals.
    # The red raid header shifts the medal positions 42 pixels to the left.
    crop=image[89:146,1147:1203]
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    white=cv2.inRange(hsv,np.array([0,0,180]),np.array([179,70,255]))
    n,_,stats,_=cv2.connectedComponentsWithStats(white)
    earned=any(w>=35 and h>=40 and area>=700 for x,y,w,h,area in stats[1:])
    reward=any('三星获得' in x['text'] and x['x']>900 and 450<x['y']<590 for x in texts)
    if earned and not reward:return 'cleared',4
    if reward and not earned:return 'uncleared',0
    return 'unknown',None

class Navigator:
    # Set on the class so callers can verify the capability without importing a
    # concrete instance; tests drive enter() through a mock device object.
    supports_collection_index=True
    supports_activity_progress=True

    def collect_activity_progress(self, profiles):
        from .collection_completion import collect
        return collect(self, profiles)

    def __init__(self,device,vision,core=None):
        self.d=device;self.v=vision;self.catalog=Catalog()
        self.core=core
        self.collection_index=None
        self._completed_activities={}
        self._full_activity_skip_enabled=False

    def _maybe_skip_full_activity(self,prefix,observation=None):
        """Used only for task entry, never battle recovery or clear verification."""
        if getattr(self,'_full_activity_skip_enabled',False) is not True:return
        from .activity_completion import activity_base,inspect_full_counter,ActivityAlreadyComplete
        base=activity_base(prefix,CHAPTERS)
        if base is None:return
        check_stop()
        cache=getattr(self,'_completed_activities',{})
        if isinstance(cache,dict) and base in cache:
            raise ActivityAlreadyComplete(cache[base])
        first=observation if observation is not None else self.observe()
        reading=inspect_full_counter(*first,prefix,CHAPTERS,self.v)
        if reading is None:return
        # Demand a new settled frame, without clicking the entry or a map node.
        self.d.invalidate_frame()
        check_stop()
        second=self.observe()
        recheck=inspect_full_counter(*second,prefix,CHAPTERS,self.v)
        if recheck is None:return
        if any(reading[k]!=recheck[k] for k in ('base_group','screen_kind','current','total')):return
        check_stop()
        import hashlib
        folder=DATA/'screens'/'activity-complete'/f'{time.time_ns()}-{base}'
        folder.mkdir(parents=True,exist_ok=False)
        frames=[]
        for index,observed in enumerate((first,second),1):
            ok,data=cv2.imencode('.png',observed[0])
            if not ok:raise RuntimeError('活动满进度截图无法保存，未跳过活动')
            path=folder/f'{index}.png';payload=data.tobytes();path.write_bytes(payload)
            frames.append({'path':str(path),'sha256':hashlib.sha256(payload).hexdigest()})
        proof={'schema':1,'source':'activity_counter','base_group':base,
               'current':reading['current'],'total':reading['total'],'at':now(),
               'observations':[reading,recheck],'frames':frames,
               'evidence':str(folder/'proof.json'),
               'run_id':read_json(DATA/'status.json',{}).get('run_id'),
               'new_clear':False}
        write_json(folder/'proof.json',proof)
        if not isinstance(cache,dict):cache={}
        cache[base]=proof;self._completed_activities=cache
        raise ActivityAlreadyComplete(proof)

    def observe(self,name=None):
        from .home_observation import navigation_regions,blocked_overlay,navigation_texts
        from .motion import UnsettledFrameError,textured_regions
        from . import sn_detail
        ocr_retry=False;settle_retries=0;map_recheck=False
        for attempt in range(4):
            try:
                selected_page=None;selected_sn=None
                candidate=None;labels=[];regions=()
                if getattr(self.d,'_needs_settle',True) and not getattr(self.d,'_settle_rois',()):
                    # This first frame only selects a known page's fixed regions.
                    # No click/arrival decision is made until fresh stable frames
                    # and the same page identity have both been confirmed.
                    candidate=self.d.shot();labels=navigation_texts(self.v,candidate)
                    reason=blocked_overlay(labels)
                    if reason:raise RuntimeError(reason)
                    page,regions=navigation_regions(labels,candidate)
                    if regions and page in ('map','is_overview'):
                        usable=textured_regions(candidate,regions)
                        if len(usable)<2:
                            # A box sampled from a panned or fading frame can land
                            # on blank pixels. The map gate would then never confirm
                            # and the whole deadline would be wasted, so re-select
                            # once from a fresh frame before giving up on it.
                            candidate=self.d.shot();labels=navigation_texts(self.v,candidate)
                            reason=blocked_overlay(labels)
                            if reason:raise RuntimeError(reason)
                            page,regions=navigation_regions(labels,candidate)
                            usable=textured_regions(candidate,regions) if regions else ()
                        if len(usable)>=2:
                            regions=usable
                        else:
                            log('地图标签未能提供两个有纹理的锚点，本次改用整帧静止检查')
                            page=None;regions=()
                    if regions:
                        if page=='sn_detail':selected_sn=sn_detail.classify(labels)
                        if page in ('map','is_overview','sn_detail','sn_map'):self.d.invalidate_frame(regions,kind='map')
                        else:self.d.invalidate_frame(regions)
                        selected_page=page
                im=self.d.stable_shot(DATA/'screens'/f'{name}.png' if name else None)
                texts=navigation_texts(self.v,im)
                reason=blocked_overlay(texts)
                if reason:raise RuntimeError(reason)
                observed_page=navigation_regions(texts,im)[0] if selected_page or map_recheck else None
                if selected_page and observed_page!=selected_page:
                    self.d.invalidate_frame()
                    # Panning can briefly hide one of the labels used to select
                    # map anchors. Discard the observation, then allow one new
                    # fully settled map observation; never replay the pan or
                    # accept a different page as the expected map.
                    if selected_page=='map' and not map_recheck and attempt<3:
                        map_recheck=True
                        log('地图标签在观察期间变化，丢弃旧定位并重新确认一次')
                        continue
                    raise RuntimeError('导航页面在观察期间发生变化，请待页面显示完成后重试')
                if map_recheck and observed_page!='map':
                    self.d.invalidate_frame()
                    raise RuntimeError('地图页面在重新观察后仍未确认，未继续操作')
                if selected_sn and not sn_detail.revalidate(selected_sn,texts).ok:
                    self.d.invalidate_frame()
                    raise RuntimeError('SN 详情关卡在观察期间变化，未继续操作')
                return im,texts
            except OCRBackendChanged:
                self.d.invalidate_frame()
                if ocr_retry or attempt==3:raise
                ocr_retry=True
                log('OCR 已回退 CPU，丢弃旧识别帧并重新截图')
            except UnsettledFrameError:
                # A candidate sampled during inertial scrolling can anchor a
                # label's old coordinates. Discard those coordinates and choose
                # regions from a fresh OCR frame once; every region still needs
                # two stable comparisons and the final page identity check.
                self.d.invalidate_frame()
                if isinstance(candidate,np.ndarray) and candidate.size:
                    folder=DATA/'screens'/'observation';folder.mkdir(parents=True,exist_ok=True)
                    snapshot=folder/f'{time.time_ns()}.png'
                    cv2.imencode('.png',candidate)[1].tofile(str(snapshot))
                    write_json(snapshot.with_suffix('.json'),dict(page=selected_page,texts=labels,
                               rois=regions,settle=getattr(self.d,'last_settle',None)))
                # A chapter fade may expose no labels on either early sample.
                # Permit one last fresh selection only after that unknown-page
                # case; it still has to pass the unchanged stability gate.
                limit=2 if selected_page is None else 1
                if settle_retries>=limit or attempt==3:raise
                settle_retries+=1
                log('首次静止检查未通过，重新识别当前控件位置后再观察一次')

    def click_text(self,terms,texts,exact=False):
        if isinstance(terms,str):terms=[terms]
        matches=[t for t in texts if any(norm(t['text'])==norm(s) if exact else norm(s) in norm(t['text']) for s in terms)]
        if len(matches)!=1:return False
        self.d.tap(matches[0]['x'],matches[0]['y'],1)
        return True

    def chapter_candidates(self,image,texts,profile):
        from . import collection_index,collection_ocr
        title=profile['title']
        # Exact verification, with a bounded decorative-label tolerance. A
        # single-glyph title is never satisfied by a longer label such as 废墟.
        matches=[x for x in texts
                 if collection_ocr.in_card(x) and x.get('y',0)>90
                 and collection_index.matches(x.get('text',''),title)]
        if matches:return matches
        # Use the same bounded card OCR as the index. The story-line caption
        # can be off-screen or renamed; it must not prevent verifying a title
        # the index could read. Retained DH/ZT frames cover this mismatch.
        return [text for text in collection_ocr.columns(image,self.v.texts_zoom,check_stop)
                if collection_index.matches(text.get('text',''),title)]

    def home(self):
        for index in range(8):
            im,t=self.observe()
            words=' '.join(x['text'] for x in t)
            if '采购中心' in words and '公开招募' in words:return
            if any(x['text'] in ('放弃行动','继续行动') for x in t):raise RuntimeError('游戏可能正在战斗，停止导航')
            from .dv_special import identify
            if identify(t):self.close_detail();continue
            if index<7:self.d.back()
        raise RuntimeError('未能返回游戏主页，已保存当前位置，未继续点击')

    def terminal(self):
        from .terminal_navigation import open_terminal
        return open_terminal(self)

    def stories(self):
        im,t=self.observe()
        if any('曲谱'==x['text'] for x in t):return
        self.terminal()
        for _ in range(3):
            self.d.tap(318,670,1.2)
            im,t=self.observe()
            if any(x['text']=='曲谱' for x in t):return
        raise RuntimeError('曲谱页面未确认')

    def _fast_scroll(self,direction,pages):
        """Blind, bounded scroll toward a remembered collection-list position.

        Only the position is remembered; the chapter title is still verified by
        the normal loop afterwards, so a moved list costs speed, never accuracy.
        """
        pages=max(0,min(int(pages),35))
        if not pages:return
        check_stop()
        log(f'按上次位置直接翻 {pages} 屏后核对章节标题')
        for _ in range(pages):
            check_stop()
            if direction==1:self.d.swipe(1150,590,1150,290,duration=320)
            else:self.d.swipe(1150,290,1150,590,duration=320)

    def build_collection_index(self,titles,max_pages=16):
        """One bounded OCR pass over 「乐章收录」; returns a title -> page index.

        Only positions are collected, and the pass never taps a chapter card:
        the normal per-chapter loop still verifies every title before entering.

        Each screen is recognised by the free full-frame OCR already taken by
        ``observe`` plus at most ``collection_ocr.MAX_CROPS`` resized column
        crops. Those crops are shared by every card title on the screen, so the
        cost does not grow with the number of wanted titles. The walk stops at
        the end of the list (two still frames), when every wanted title has been
        recognised, or at the hard page bound -- never merely because a run of
        screens added no new title.
        """
        from . import collection_index,collection_ocr
        wanted=[]
        for title in titles:
            folded=collection_index.key(title)
            if folded and folded not in wanted:wanted.append(folded)
        if not wanted:return None
        self.stories()
        im,t=self.observe()
        if not self.click_text('乐章收录',t,exact=True):raise RuntimeError('统一章节列表入口未确认')
        im,t=self.observe()
        if not any(x['text']=='默认进度' for x in t):raise RuntimeError('乐章收录列表未确认')
        pages={};previous=None;stationary=0;page=0;complete=False
        limit=max(1,min(int(max_pages),collection_index.MAX_PAGES))
        def absorb(rows):
            for text in rows:
                folded=collection_index.key(text.get('text',''))
                if not folded:continue
                for want in wanted:
                    if want in pages:continue
                    if collection_index.matches(text.get('text',''),want):
                        pages[want]=page
        for page in range(limit):
            check_stop()
            im,t=self.observe(f'collection-{page}')
            # Full-frame OCR is already paid for by observe(); use it before the
            # bounded resized crops.
            absorb(x for x in t if collection_ocr.in_card(x) and x.get('y',0)>90)
            if len(pages)<len(wanted):
                absorb(collection_ocr.columns(im,self.v.texts_zoom,check_stop))
            signature=cv2.resize(im[100:680],(128,58))
            stable=previous is not None and np.mean(cv2.absdiff(signature,previous))<2.5
            stationary=stationary+1 if stable else 0;previous=signature
            if stationary>=2:
                # Reaching the end of the list is what ``complete`` records.
                complete=True;break
            if len(pages)>=len(wanted):
                # Every wanted title was recognised; stop early without claiming
                # that the list traversal itself reached the bottom.
                break
            self.d.swipe(1150,590,1150,290,duration=1000)
        saved=collection_index.save(pages,page+1,complete=complete)
        log(f'乐章收录索引：识别 {len(pages)} 个标题，覆盖前 {page+1} 屏'
            + ('（已到列表末尾）' if complete else '（名录未走完，仅用于排序与定位；不据此判定缺席）'))
        return saved or (None if not pages else {'pages_by_title':pages,'pages':page+1,'complete':complete,'at':0.0})

    def enter(self,prefix):
        from .workflow import update
        from .navigation_errors import ChapterEntryUnavailable
        update('navigation',chapter=prefix,code='')
        log(f'后台导航到 {prefix}')
        if prefix=='CURRENT':return
        if prefix in CHAPTERS and getattr(self,'_full_activity_skip_enabled',False) is True:
            # Check the existing page before any zone selection or activity entry.
            self._maybe_skip_full_activity(prefix)
        if prefix in CHAPTERS and not self.catalog.stages(prefix):
            raise MapResourcesUnavailable(f'{prefix} 当前 MAA 缺少该分区的战斗地图资源，保留待确认项')
        if prefix=='CW-S':
            from .cw_morse import is_map
            if is_map(*self.observe()):
                self._last_story_base='CW'
                return
        from . import siracusa
        if prefix in siracusa.GROUPS:
            if siracusa.reuse_overview(self):return
            if prefix!='IS':self.enter('IS');return
        if prefix in CHAPTERS and getattr(self,'_last_story_base',None)==CHAPTERS[prefix].get('base',prefix):
            from .story_zones import enter_zone
            if enter_zone(self,prefix,CHAPTERS,reuse=True):return
            self._last_story_base=None
        if prefix in CHAPTERS and CHAPTERS[prefix].get('base',prefix)!=prefix:
            base=CHAPTERS[prefix]['base'];self.enter(base)
            from .story_zones import enter_zone
            if enter_zone(self,prefix,CHAPTERS):
                self._last_story_base=base
                return
            im,t=self.observe()
            if self.visible_stages(t,prefix):
                self._last_story_base=base
                return
            entries=CHAPTERS[prefix].get('zones',[])
            for label in entries:
                if self.click_text(label,[x for x in t if x['y']>570],exact=True):
                    im,t=self.observe('entry-'+prefix)
                    if self.visible_stages(t,prefix):
                        self._last_story_base=base
                        return
            raise RuntimeError(f'{prefix} 分区入口未确认，停止导航')
        if prefix.isdigit() and 0<=int(prefix)<=17:
            if self.core is None:raise RuntimeError('主线导航需要 MAA 核心连接')
            self.terminal()
            self.core.run('Custom',{'task_names':[f'Episode{int(prefix)}']},timeout=180)
            if 10<=int(prefix)<=14:self.core.run('Custom',{'task_names':['ChapterDifficultyNormal']},timeout=60)
            self.wait_for_map(prefix)
            return
        if prefix not in CHAPTERS:raise RuntimeError('此章节尚未适配自动入口；可以使用“扫描当前地图”')
        from . import nav_hints,collection_index
        profile=CHAPTERS[prefix]
        index=getattr(self,'collection_index',None)
        indexed=collection_index.page_of(index,profile['title'])
        if indexed is not None:
            # A positive index observation outranks any old negative hint, even
            # one written from an earlier (invalid) index-based absence.
            nav_hints.clear_missing(prefix)
        else:
            absent=nav_hints.missing(prefix)
            if absent is not None:
                # Only a real targeted lookup writes this hint. Re-walking the
                # list costs about two minutes, so a recent failure is bounded.
                raise ChapterEntryUnavailable(prefix,profile['title'],cached=True,age_seconds=absent)
            if isinstance(index,dict):
                # Index absence is NOT absence: decorative titles are missed by
                # OCR and ``complete`` only means the list walk ended.
                log(f'乐章收录索引未收录 {profile["title"]}（索引不据此判定缺席，仍按原流程翻查）')
        self.stories()
        im,t=self.observe()
        if not self.click_text('乐章收录',t,exact=True):raise RuntimeError('统一章节列表入口未确认')
        im,t=self.observe()
        if not any(x['text']=='默认进度' for x in t):raise RuntimeError('乐章收录列表未确认')
        # Unified collection is independent of the currently selected storyline.
        # Retry clicks after scrolling because the first tap can merely stop inertia.
        hint=nav_hints.page_hint(prefix)
        if not hint and indexed is not None:
            # The one-pass index is a good first guess when nothing is remembered.
            hint={'direction':1,'pages':indexed}
        if hint and hint['pages']:
            self._fast_scroll(hint['direction'],hint['pages'])
        found=False;positive=False;direction=1;pages=0;entry_attempts=0
        for direction in (1,-1):
            previous=None;stationary=0;pages=0
            for _ in range(35):
                check_stop();im,t=self.observe()
                # A lost/changed list is a navigation failure, not evidence that
                # this particular chapter is unavailable. Never cache it as a
                # local miss or click a matching title on an unrelated page.
                if not any(x.get('text')=='默认进度' for x in t):
                    raise RuntimeError('章节检索期间未确认乐章收录列表，未继续操作或记录缺席')
                title=self.chapter_candidates(im,t,profile)
                if title:
                    # The decorative title was positively recognised. Entry may
                    # still fail; that must never be recorded as title absence.
                    positive=True
                    entry_attempts+=1
                    self.d.tap(title[0]['x'],title[0]['y'],1.4)
                    im,t=self.observe()
                    if not any(x['text']=='默认进度' for x in t):found=True;break
                    if entry_attempts>=ENTRY_TAP_LIMIT:
                        raise RuntimeError(
                            f'已识别章节 {profile["title"]}，但连续 {ENTRY_TAP_LIMIT} 次点击后仍停留在名录；'
                            '章节入口未确认，未标记为缺席')
                    continue
                signature=cv2.resize(im[100:680],(128,58))
                stable=previous is not None and np.mean(cv2.absdiff(signature,previous))<2.5
                stationary=stationary+1 if stable else 0;previous=signature
                if stationary>=2:break
                if direction==1:self.d.swipe(1150,590,1150,290,duration=1000)
                else:self.d.swipe(1150,290,1150,590,duration=1000)
                pages+=1
            if found:break
        if not found:
            if positive:
                raise RuntimeError(f'已识别章节 {profile["title"]}，但章节入口未确认；未标记为缺席')
            # A real targeted walk of both directions failed. This is a bounded
            # cache entry, not proof that the chapter does not exist.
            nav_hints.mark_missing(prefix,source=nav_hints.SOURCE_SEARCH)
            raise ChapterEntryUnavailable(prefix,profile['title'])
        if hint and hint['direction']==direction:pages+=hint['pages']
        nav_hints.remember_page(prefix,direction,pages)
        for _ in range(5):
            im,t=self.observe('entry-'+prefix)
            self._maybe_skip_full_activity(prefix,(im,t))
            if prefix=='IS' and siracusa.is_overview(t):
                self._last_story_base='IS'
                return
            if self.visible_stages(t,prefix):
                self._last_story_base=profile.get('base',prefix)
                return
            if self.click_text('进入活动',t):continue
            if self.click_text(['开始探索','进入关卡'],t):continue
            if any(self.click_text(zone,t,exact=True) for zone in profile.get('zones',[])):continue
            # Some SideStory home screens use their own named normal-zone buttons.
            if prefix=='CW' and self.click_text(['特里蒙','调查'],t):continue
            if self.click_text(profile['title'],t,exact=True):continue
            raise RuntimeError('已到章节，但关卡地图入口未适配；保留截图')
        raise RuntimeError('章节入口超时')

    def visible_stages(self,texts,prefix):
        # The result page also displays the exact operation code. It is not a map.
        if is_settlement(texts) or detail_mode(texts):return []
        result=[];allowed=set(self.catalog.stages(prefix))
        for t in texts:
            code=norm(t['text']).upper()
            if code in allowed and t['x']<1250 and 90<t['y']<610:
                if self.catalog.code(code)==code:result.append({**t,'code':code})
        return result

    def stage_candidates(self,image,texts,prefix,target=None):
        """Exact catalogue codes only; local 2x OCR is a bounded fallback."""
        found=self.visible_stages(texts,prefix)
        if (found and target is None) or any(x['code']==target for x in found):return found
        if is_settlement(texts) or detail_mode(texts):return found
        # Tiles preserve small glyphs that full-screen detector resizing loses.
        # Overlap keeps a code crossing a tile boundary intact.
        for top in (90,310):
            for left in (0,400,800):
                extra=self.v.texts_zoom(image,(left,top,480,300),scale=2)
                for node in self.visible_stages(extra,prefix):
                    if not any(n['code']==node['code'] and abs(n['x']-node['x'])<45 and abs(n['y']-node['y'])<30 for n in found):
                        found.append(node)
                if target is not None and any(x['code']==target for x in found):return found
        return found

    def detail(self,code,expected_mode=None):
        im,t=self.observe('inspect-'+code)
        if code in ('DV-S-1','DV-S-2'):
            from .dv_special import identify
            if expected_mode not in (None,'normal') or identify(t,code)!=code:
                raise RuntimeError('DV-S 特殊关卡详情编号或难度未确认')
            result=dict(code=code,mode='normal',layout='dv_special',state='unknown',stars=None,cost=0,
                at=now(),evidence=str(DATA/'screens'/f'inspect-{code}.png'),
                reason='特殊面板未显示可验证的通关标记；已解锁及查看条件不作为通关证据',texts=[x['text'] for x in t])
            self.save_stage(result)
            return result
        mode=detail_mode(t)
        if expected_mode and mode!=expected_mode:raise RuntimeError('关卡难度未确认，禁止继续')
        mode=mode or 'normal'
        layout='sn' if code.startswith('SN-') and any(x['text']=='开始演习' for x in t) else 'standard'
        full_header=([x for x in t if 80<x['x']<330 and 530<x['y']<567] if layout=='sn'
                     else [x for x in t if 800<x['x']<1010 and 65<x['y']<112])
        stage_roi=full_header
        roi=(70,525,260,50) if layout=='sn' else (760,65,260,65)
        if not header_matches(stage_roi,code):
            stage_roi=self.v.texts(im,roi)
        if not header_matches(stage_roi,code):
            # OCR occasionally misses the light-on-dark code. Retry enlarged crop.
            x,y,w,h=roi;extra=self.v.texts(cv2.resize(im[y:y+h,x:x+w],None,fx=2,fy=2))
            # Some narrow digit glyphs disappear from OCR. Accept a unique official
            # title only when the same header also contains a compatible code fragment.
            titles=([x['text'] for x in t if 80<x['x']<330 and 567<x['y']<610] if layout=='sn'
                    else [x['text'] for x in t if 820<x['x']<1010 and 112<x['y']<160])
            unique_title=any(self.catalog.code(s)==code for s in titles)
            header_texts=stage_roi+full_header
            fragments=[norm(x['text']).upper() for x in header_texts if norm(x['text']).upper()!='OPERATION']
            compatible=any(len(s)>=2 and '-' in s and
                           (s in code or (s.endswith(code) and not re.search(r'\d',s[:-len(code)]))) for s in fragments)
            from .stage_header import contrast_header
            if not header_matches(extra,code) and not (unique_title and compatible) and not header_matches(contrast_header(self.v,im,code,mode=mode),code):
                raise RuntimeError(f'关卡详情编号未确认：{code}')
        words=' '.join(x['text'] for x in t)
        if not any(s in words for s in ('开始行动','开始作战','开始突袭')):
            result={'code':code,'mode':mode,'state':'locked' if '解锁' in words else 'unknown','at':now(),
                    'evidence':str(DATA/'screens'/f'inspect-{code}.png')}
            self.save_stage(result)
            return result
        state,stars,components=classify_stars(im,layout)
        if mode=='raid':state,stars=classify_raid(im,t)
        costs=[int(norm(x['text'])[1:]) for x in t if x['x']>1080 and x['y']>(650 if layout=='sn' else 675) and re.fullmatch(r'-\d+',norm(x['text']))]
        cost=costs[0] if len(costs)==1 else stage_info(code,mode).get('cost',self.catalog.costs.get(code))
        # A verified first-clear reward label plus no earned medals is positive uncleared evidence.
        reward_words=' '.join(x['text'] for x in t if x['x']>850 and x['y']>430)
        first=any(s in reward_words for s in ('首次通关','首次掉落','首次报酬'))
        if mode=='normal' and state=='unknown' and (first or any('三星获得' in x['text'] and x['x']>900 and 450<x['y']<590 for x in t)):state='uncleared'
        suffix='-raid' if mode=='raid' else ''
        shot=DATA/'screens'/f'{code}{suffix}-detail.png';cv2.imencode('.png',im)[1].tofile(str(shot))
        result={'code':code,'mode':mode,'layout':layout,'state':state,'stars':stars,'at':now(),'evidence':str(shot),'medals':components,'first_clear_label':first,'cost':cost,'texts':[x['text'] for x in t]}
        self.save_stage(result)
        return result

    def save_stage(self,result):
        db=read_json(DATA/'progress.json',{})
        db[result['code']+':'+result['mode']]=result;write_json(DATA/'progress.json',db)
        callback=getattr(self,'on_scan_stage',None)
        if callable(callback):callback(result)

    def set_mode(self,mode):
        if mode not in ('normal','raid'):raise ValueError('未支持的难度')
        for _ in range(3):
            im,t=self.observe()
            if detail_mode(t)==mode:return
            labels=['突袭模式'] if mode=='raid' else ['普通难度','普通模式']
            if not self.click_text(labels,[x for x in t if x['y']>600],exact=True):break
            self.d.invalidate_frame(((760,65,460,110),(1000,600,270,115)))
            self.await_state(lambda image,texts:detail_mode(texts)==mode,'切换难度 '+mode)
            return
        raise RuntimeError(f'无法确认目标难度 {mode}')

    def await_state(self, predicate, label, attempts=4):
        """Observe a bounded postcondition; no repeated input during a transition."""
        from .common import STOP
        for attempt in range(attempts):
            check_stop()
            image,texts=self.observe()
            try:
                if predicate(image,texts):return image,texts
            except OCRBackendChanged:
                self.d.invalidate_frame()
            if attempt+1<attempts:
                self.d.invalidate_frame()
                STOP.wait(.25)
        folder=DATA/'screens'/'state-timeouts'
        folder.mkdir(parents=True,exist_ok=True)
        shot=folder/f'{time.time_ns()}.png'
        cv2.imencode('.png',image)[1].tofile(str(shot))
        write_json(shot.with_suffix('.json'),{'at':now(),'expected':label,'texts':texts})
        raise RuntimeError('操作后的页面未确认：'+label)

    def _detail_identity(self,image,texts,code):
        if is_settlement(texts):return False
        if code in ('DV-S-1','DV-S-2'):
            from .dv_special import identify
            return identify(texts,code)==code
        if not detail_mode(texts) and not any('解锁' in t['text'] for t in texts):return False
        roi=(70,525,260,85) if code.startswith('SN-') else (760,65,260,65)
        x,y,w,h=roi
        header=[t for t in texts if x<t['x']<x+w and y<t['y']<y+h]
        if header_matches(header,code):return True
        if header_matches(self.v.texts_zoom(image,roi,2),code):return True
        from .stage_header import contrast_header
        if header_matches(contrast_header(self.v,image,code,mode=detail_mode(texts) or 'normal'),code):return True
        # Preserve detail()'s independent official-title + compatible-fragment gate.
        # Decorative raid headers can merge OPERATION with the exact stage code.
        title_roi=(80,567,250,43) if code.startswith('SN-') else (800,112,220,48)
        tx,ty,tw,th=title_roi
        unique_title=any(self.catalog.code(t['text'])==code for t in texts
                         if tx<t['x']<tx+tw and ty<t['y']<ty+th)
        fragments=[norm(t['text']).upper() for t in header if norm(t['text']).upper()!='OPERATION']
        compatible=any(len(value)>=2 and '-' in value and (value in code or
                       (value.endswith(code) and not re.search(r'\d',value[:-len(code)]))) for value in fragments)
        return unique_title and compatible

    def close_detail(self):
        im,t=self.observe()
        from .dv_special import exit_target
        special_exit=exit_target(t)
        if not detail_mode(t):return
        # A fixed blank-map coordinate can now contain another stage node.
        # Android back closes the confirmed detail panel without selecting it.
        if special_exit:self.d.tap(special_exit['x'],special_exit['y'],.2)
        else:self.d.back()
        self.d.invalidate_frame()
        def map_ready(image,texts):
            if detail_mode(texts) or is_settlement(texts):return False
            from .siracusa import is_overview
            if is_overview(texts):return True
            from .cw_morse import is_map
            if is_map(image,texts):return True
            return any(90<x['y']<610 and self.catalog.code(norm(x['text']).upper())==norm(x['text']).upper()
                       for x in texts if norm(x['text']))
        self.await_state(map_ready,'关闭关卡详情并返回地图')

    def tap_stage(self,node,code):
        offset=-24 if code.startswith('SN-') else 0
        self.d.tap(node['x'],node['y']+offset,.2)
        rois=((70,525,260,150),(1000,600,270,115)) if code.startswith('SN-') else ((760,65,460,110),(1000,600,270,115))
        if code in ('DV-S-1','DV-S-2'):rois=((775,160,412,62),(1120,48,120,60),(775,568,412,46))
        # SN's lower panel has animated borders. Select its exact text/control
        # anchors from fresh OCR after arrival, never the old broad panel ROI.
        self.d.invalidate_frame(() if code.startswith('SN-') else rois)
        self.await_state(lambda image,texts:self._detail_identity(image,texts,code),code+' 关卡详情')

    def find(self,code,max_swipes=18,mode='normal'):
        if self.catalog.code(code)!=code:raise ValueError(f'{code} 不在战斗地图索引中，可能是剧情节点')
        from . import cw_morse
        if code in cw_morse.POINTS:return cw_morse.find(self,code,mode)
        from . import siracusa
        if code in siracusa.POINTS:return siracusa.find(self,code,max_swipes,mode)
        self.close_detail();prefix=stage_info(code).get('group',code.rsplit('-',1)[0])
        number=int(re.findall(r'\d+',code)[-1])
        early=number<=max(1,len(self.catalog.stages(prefix))//2)
        from .map_traversal import PanBoundary
        from .home_observation import map_codes
        for direction in ((-1,1) if early else (1,-1)):
            boundary=PanBoundary(map_codes())
            for _ in range(max_swipes):
                check_stop();im,t=self.observe()
                match=[x for x in self.stage_candidates(im,t,prefix,target=code) if x['code']==code]
                if match:
                    self.tap_stage(match[0],code)
                    try:self.set_mode(mode);return self.detail(code,mode)
                    except RuntimeError:
                        im,t=self.observe('retry-'+code)
                        retry=[x for x in self.visible_stages(t,prefix) if x['code']==code]
                        if retry:
                            self.tap_stage(retry[0],code)
                            self.set_mode(mode);return self.detail(code,mode)
                        raise
                if boundary.observe(im,t):break
                # A fast 700 px fling can travel farther than a screen after
                # release. Overlapping slow drags keep narrow labels (5-8 at
                # the edge in the retained run) inside an observed viewport.
                if direction==1:self.d.swipe(900,380,500,380,duration=900)
                else:self.d.swipe(500,380,900,380,duration=900)
        raise StageNotFound(f'当前地图未找到 {code}，未标记为未通关')

    def wait_for_map(self,prefix,attempts=6):
        """MAA navigation can finish before the map's fade-in has completed."""
        for attempt in range(attempts):
            check_stop();_,texts=self.observe('main-'+prefix)
            if self.visible_stages(texts,prefix):return
            if attempt+1<attempts:time.sleep(.6)
        raise RuntimeError('MAA 已执行主线导航，但未确认目标关卡地图')

    def scan(self,prefix,max_pages=18,include_raid=True,incremental=False,only_missing=False):
        from .workflow import update
        update('scan',chapter=prefix,code='')
        if prefix=='CW-S':
            from . import cw_morse
            return cw_morse.scan(self,include_raid,incremental,only_missing)
        from . import siracusa
        if prefix in siracusa.GROUPS:
            return siracusa.scan(self,prefix,include_raid,incremental,only_missing)
        self.close_detail();seen=set();results=[]
        # After a verified clear, revisit locked/unknown/uncleared nodes only.
        # A normal clear never implies a raid clear. Initial account scans stay full.
        db=read_json(DATA/'progress.json',{}) if incremental else {}
        def reusable(row,code,mode):
            if only_missing:
                from .scan_progress import _is_confirmed
                return _is_confirmed(row,{'code':code,'mode':mode})
            return row.get('state')=='cleared'
        for code in self.catalog.stages(prefix):
            normal=db.get(code+':normal',{})
            raid=db.get(code+':raid',{})
            if reusable(normal,code,'normal') and (not include_raid or not stage_info(code,'raid') or reusable(raid,code,'raid')):
                seen.add(code);results.append(normal)
                if include_raid and reusable(raid,code,'raid'):results.append(raid)
        if len(seen)==len(self.catalog.stages(prefix)):
            log(f'{prefix} 已有完整'+('扫描记录' if only_missing else '通关证据')+'，跳过重复扫描');return results
        if seen:log(f'{prefix} 增量扫描：复用 {len(seen)} 个'+('已确认节点' if only_missing else '已通关节点')+'，检查剩余进度')
        # Sweep toward the beginning before scanning to the end.
        from .map_traversal import PanBoundary
        from .home_observation import map_codes
        boundary=PanBoundary(map_codes())
        for _ in range(8):
            check_stop();image,texts=self.observe()
            if boundary.observe(image,texts):break
            self.d.swipe(350,380,1050,380)
        boundary=PanBoundary(map_codes())
        for page in range(max_pages):
            check_stop();im,t=self.observe(f'{prefix}-map-{page}');visible=self.visible_stages(t,prefix)
            at_boundary=boundary.observe(im,t)
            for code in [x['code'] for x in visible if x['code'] not in seen]:
                update('scan',code=code,mode='normal')
                im,current=self.observe();matches=[x for x in self.visible_stages(current,prefix) if x['code']==code]
                if not matches:continue
                try:
                    # Opening/closing details may recenter the map. Evidence
                    # from before that transition cannot end the next sweep.
                    boundary.reset();at_boundary=False
                    self.tap_stage(matches[0],code)
                    self.set_mode('normal');row=self.detail(code,'normal');results.append(row);seen.add(code);log(f'关卡 {code}: {row["state"]}')
                    if include_raid and row['state']=='cleared':
                        _,details=self.observe()
                        if any(x['text']=='突袭模式' for x in details):
                            update('scan',code=code,mode='raid')
                            self.set_mode('raid');raid=self.detail(code,'raid');results.append(raid);log(f'关卡 {code} 突袭: {raid["state"]}');self.set_mode('normal')
                except RuntimeError as e:
                    log(f'{code} 本节点扫描未完成：{e}；确认返回地图后继续')
                    self.close_detail()
                    self.await_state(lambda image,texts:not detail_mode(texts) and bool(self.visible_stages(texts,prefix)),
                                     code+' 扫描失败后返回本分区地图')
                    continue
                self.close_detail()
            if at_boundary:break
            self.d.swipe(1000,380,550,380)
        # A second bounded pass handles nodes displaced by opening detail panels.
        for code in self.catalog.stages(prefix):
            if code in seen:continue
            update('scan',code=code,mode='normal')
            try:
                row=self.find(code,max_swipes=8);results.append(row);seen.add(code)
                log(f'补充确认 {code}: {row["state"]}')
                if include_raid and row['state']=='cleared':
                    _,details=self.observe()
                    if any(x['text']=='突袭模式' for x in details):
                        self.set_mode('raid');results.append(self.detail(code,'raid'));self.set_mode('normal')
                self.close_detail()
            except RuntimeError as e:log(str(e))
        # Catalogue-only entries are unknown; unseen does not mean locked/uncleared.
        db=read_json(DATA/'progress.json',{})
        for code in self.catalog.stages(prefix):
            if code+':normal' not in db:db[code+':normal']={'code':code,'mode':'normal','state':'unseen','at':now()}
        write_json(DATA/'progress.json',db)
        log(f'{prefix} 本次扫描结束，实际读取 {len(results)} 条记录；完整性由关卡目录核对')
        return results
