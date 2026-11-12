"""Resumable scan-only orchestration. Never searches jobs or dispatches battles."""
import csv,time
from .common import (DATA,read_json,write_json,now,check_stop,log,DeviceUnavailableError,
                     is_transient_device_error,settings)
from .campaign import catalogue,export_coverage
from .scan_progress import plan_groups,arrange_groups,summarize_scan,summarize_group
from . import workflow
from .nav_hints import FILE as NAV_HINTS_FILE
from .terminal_navigation import TerminalNavigationError

# A dropped screenshot stream or a re-enumerated emulator can recover on its
# own. The budget is finite and only ever spent on read-only scanning: a real
# offline device, an authorisation problem or a user stop still ends the run.
RECOVERY_ENABLED=True
RECOVERY_ATTEMPTS=3
RECOVERY_WAITS=(10.,20.,30.)
RECOVERY_POLL=2.0


def recovery_plan():
    """Return (enabled, attempts, waits) with optional settings.json overrides."""
    conf=settings().get('device_recovery') if isinstance(settings(),dict) else None
    if not isinstance(conf,dict):conf={}
    attempts=conf.get('attempts',RECOVERY_ATTEMPTS)
    waits=conf.get('waits',list(RECOVERY_WAITS))
    try:attempts=max(0,int(attempts))
    except (TypeError,ValueError):attempts=RECOVERY_ATTEMPTS
    try:waits=[max(0.,float(w)) for w in waits]
    except (TypeError,ValueError):waits=list(RECOVERY_WAITS)
    if not waits:waits=list(RECOVERY_WAITS)
    return bool(conf.get('enabled',RECOVERY_ENABLED)),attempts,waits


def _sleep(seconds):
    deadline=time.monotonic()+max(0.,float(seconds))
    while True:
        check_stop()
        remaining=deadline-time.monotonic()
        if remaining<=0:return
        time.sleep(min(.2,remaining))


def _device_ready(nav):
    """Probe the same emulator. Unknown probes never block a documented retry."""
    probe=getattr(getattr(nav,'d',None),'ready',None)
    if not callable(probe):return True
    try:return bool(probe())
    except InterruptedError:raise
    except Exception:return False


def wait_for_device(nav,seconds):
    """Poll read-only until the emulator answers or the budget is exhausted."""
    deadline=time.monotonic()+max(0.,float(seconds))
    while True:
        check_stop()
        if _device_ready(nav):return True
        remaining=deadline-time.monotonic()
        if remaining<=0:return False
        _sleep(min(RECOVERY_POLL,remaining))



def export_pending(book,progress,summary):
    keys={key for group in summary['groups'].values() for key in group['pending_keys']}
    rows=[{**book['stages'][key],**progress[key]} for key in sorted(keys)]
    write_json(DATA/'uncleared.json',{'at':now(),'scan_complete':summary['complete'],
        'confirmed':summary['confirmed'],'scan_total':summary['total'],'unsupported':summary['unsupported'],'rows':rows})
    with (DATA/'uncleared.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['关卡','难度','状态','章节','分区','观察时间','截图证据'])
        labels={'normal':'普通','raid':'突袭','partial':'未满星','uncleared':'未通关'}
        for row in rows:writer.writerow([row['code'],labels.get(row['mode'],row['mode']),labels.get(row['state'],row['state']),
            row.get('chapter',''),row.get('group',''),row.get('at',''),row.get('evidence','')])


def _side_story_groups(groups):
    return [g for g in groups if not str(g).isdigit() and g != 'CURRENT']


def _chapter_titles():
    from .navigation import CHAPTERS
    return CHAPTERS


def ensure_collection_index(nav, groups):
    """Load or build the one-pass 乐章收录 index; None when it is not usable.

    The capability check is on the class, not the instance, so a test double can
    never pretend to support it and silently reorder a planned round.
    """
    from . import collection_index
    if not _side_story_groups(groups):
        return None
    if not getattr(type(nav), 'supports_collection_index', False):
        return None
    conf = settings().get('collection_index') if isinstance(settings(), dict) else None
    if not isinstance(conf, dict):
        conf = {}
    if conf.get('enabled') is False:
        return None
    cached = collection_index.load()
    if cached:
        log(f'复用乐章收录索引：{len(cached["pages_by_title"])} 个标题')
        return cached
    titles = [(_chapter_titles().get(prefix) or {}).get('title') for prefix in _side_story_groups(groups)]
    titles = [title for title in titles if title]
    if not titles:
        return None
    log('建立乐章收录索引：整轮只翻一次名录，之后按名录顺序扫描')
    return nav.build_collection_index(titles, max_pages=int(conf.get('max_pages', collection_index.MAX_PAGES)))


def order_by_index(groups, index):
    """Main chapters first, then side stories in the order the list shows them."""
    from . import collection_index
    def key(item):
        position, prefix = item
        if str(prefix).isdigit():
            return (0, 0, position)
        page = collection_index.page_of(index, (_chapter_titles().get(prefix) or {}).get('title', ''))
        return (1, page if page is not None else 10 ** 6, position)
    return [prefix for _, prefix in sorted(enumerate(groups), key=key)]


def carried_failures(previous,groups):
    """Group entry failures from earlier rounds that still have scan work.

    Reports older than 0.9.29 have no ``failed_groups``; their per-chapter
    errors from the last round are carried over the same way.
    """
    if not isinstance(previous,dict):return {}
    planned=set(groups);carried={}
    def local_failure(entry):
        # Earlier versions recorded a shared Terminal outage against each
        # subsequent chapter. Those errors say nothing about its own entry.
        reason=str(entry.get('error','')) if isinstance(entry,dict) else str(entry)
        return not any(text in reason for text in ('终端页面未确认','进入终端前无法确认主页',
                                                    '终端观察失败','未能返回游戏主页'))
    chapters=previous.get('chapters')
    for entry in chapters if isinstance(chapters,list) else ():
        if isinstance(entry,dict) and entry.get('error') and entry.get('chapter') in planned and local_failure(entry):
            carried[entry['chapter']]={'error':str(entry['error']),'at':entry.get('at','')}
    failed=previous.get('failed_groups')
    if isinstance(failed,dict):
        carried.update((name,entry) for name,entry in failed.items()
                       if isinstance(name,str) and name in planned and local_failure(entry))
    return carried


def absent_chapters():
    """Chapters recorded absent from 乐章收录 (any age); read-only, never raises."""
    try:data=read_json(DATA/NAV_HINTS_FILE,{})
    except (OSError,ValueError):return set()
    missing=data.get('missing') if isinstance(data,dict) else None
    return {name for name in missing if isinstance(name,str)} if isinstance(missing,dict) else set()


def unreachable_groups(groups,failed,absent):
    """Groups whose own entry failed, or whose base chapter is recorded absent."""
    titles=_chapter_titles()
    def base(prefix):return (titles.get(prefix) or {}).get('base',prefix)
    return {g for g in groups if g in failed or g in absent or base(g) in absent}


def _brief(names,limit=8):
    names=list(names)
    return '、'.join(names[:limit])+(f' 等 {len(names)} 个' if len(names)>limit else '')


def scan_all(nav):
    book=catalogue();previous=read_json(DATA/'scan-report.json',{})
    if not isinstance(previous,dict):previous={}
    if previous:write_json(DATA/'backups'/f'scan-{time.time_ns()}.json',previous)
    progress=read_json(DATA/'progress.json',{})
    groups=plan_groups(book,progress,previous)
    report={'schema':2,'started':previous.get('started',now()),'at':now(),'state':'running',
            'active_group':previous.get('active_group') if previous.get('active_group') in groups else None,
            'planned_groups':groups,'chapters':[],'reused_groups':len(book['profiles'])-len(groups),
            'failed_groups':carried_failures(previous,groups),'deferred_groups':[]}
    old_callback=getattr(nav,'on_scan_stage',None)
    def persist(state=None):
        progress=read_json(DATA/'progress.json',{});summary=summarize_scan(book,progress)
        report.update(at=now(),summary=summary)
        if state:report['state']=state
        write_json(DATA/'scan-report.json',report);export_pending(book,progress,summary)
        return summary
    def on_stage(row):
        summary=persist()
        workflow.update('scan',chapter=report['active_group'],code=row['code'],mode=row['mode'],
            completed=summary['confirmed'],total=summary['total'],
            message=f'仅扫描：已确认 {summary["confirmed"]}/{summary["total"]} 项，发现 {summary["pending"]} 项未通关或未满星；记录已保存')
    nav.on_scan_stage=on_stage
    try:
        summary=persist()
        enabled,attempt_limit,waits=recovery_plan()
        recoveries=0
        index=None
        try:
            index=ensure_collection_index(nav,groups)
        except TerminalNavigationError:
            from .startup import recover_scan_startup
            if getattr(nav,'startup_recovery',False) is not True or not recover_scan_startup(nav):raise
            index=ensure_collection_index(nav,groups)
        except (InterruptedError,DeviceUnavailableError):raise
        except Exception as exc:
            log(f'乐章收录索引未建立：{exc}；按原计划顺序扫描')
            index=None
        if isinstance(index,dict):
            nav.collection_index=index
            ordered=order_by_index(groups,index)
            if ordered!=groups:
                log(f'按乐章收录名录顺序重排 {len(ordered)} 个分区')
                groups=ordered
        # Applied after the index order, which would otherwise undo the resume.
        arranged,deferred=arrange_groups(groups,book,progress,previous,
                                         unreachable_groups(groups,report['failed_groups'],absent_chapters()))
        active=previous.get('active_group')
        if arranged and arranged[0]==active and active not in deferred:
            log(f'从上次中断的分区 {active} 继续扫描')
        if deferred:
            log(f'{_brief(deferred)} 剩余关卡上轮已在地图上找不到、需先通关普通难度或分区入口未找到，放到本轮最后再查')
        groups=arranged
        report.update(planned_groups=groups,deferred_groups=deferred)
        persist()
        for prefix in groups:
            check_stop();report['active_group']=prefix;persist()
            workflow.update('navigation',chapter=prefix,code='',completed=summary['confirmed'],total=summary['total'],
                message=f'仅扫描：进入 {prefix}，复用已确认记录，继续识别缺项')
            error=None
            navigation_unchanged=False
            startup_recovered=False
            while True:
                try:
                    nav.enter(prefix);nav.scan(prefix,incremental=True,only_missing=True)
                    break
                except InterruptedError:raise
                except TerminalNavigationError:
                    from .startup import recover_scan_startup
                    if (startup_recovered or getattr(nav,'startup_recovery',False) is not True
                            or not recover_scan_startup(nav)):raise
                    startup_recovered=True
                    continue
                except DeviceUnavailableError as exc:
                    # Saved records stay authoritative; only the live round is lost.
                    if not enabled or not is_transient_device_error(exc):raise
                    recoveries+=1
                    if recoveries>attempt_limit:
                        log(f'设备连接中断 {recoveries} 次，超过自动恢复上限；保留已确认记录并停止本轮')
                        raise
                    wait=waits[min(recoveries,len(waits))-1]
                    log(f'设备连接中断，尝试自动恢复 {recoveries}/{attempt_limit}，最多等待 {wait:g} 秒：{exc}')
                    workflow.update('navigation',chapter=prefix,code='',completed=summary['confirmed'],
                        total=summary['total'],message=f'设备连接中断，正在等待模拟器恢复（{recoveries}/{attempt_limit}）')
                    persist('running')
                    if not wait_for_device(nav,wait):
                        log('模拟器未在限定时间内恢复；保留已确认记录并停止本轮')
                        raise
                    log(f'模拟器已恢复；重新确认页面后继续分区 {prefix}')
                    try:nav.home()
                    except InterruptedError:raise
                    except Exception as home_error:log(f'恢复后返回主页未确认：{home_error}；仍继续尝试 {prefix}')
                    continue
                except RuntimeError as exc:
                    if getattr(exc,'navigation_unchanged',False) is True:
                        navigation_unchanged=True
                        error=str(exc);log(f'{prefix} 扫描未完整：{error}');break
                    if not startup_recovered and getattr(nav,'startup_recovery',False) is True:
                        from .startup import recover_scan_startup
                        if recover_scan_startup(nav):
                            startup_recovered=True
                            continue
                    error=str(exc);log(f'{prefix} 扫描未完整：{error}；保留已确认记录');break
            current=summarize_group(book,read_json(DATA/'progress.json',{}),prefix)
            entry={'chapter':prefix,'at':now(),**current}
            if error:
                entry['error']=error
                report['failed_groups'][prefix]={'error':error,'at':entry['at']}
            else:report['failed_groups'].pop(prefix,None)
            report['chapters'].append(entry);summary=persist();export_coverage()
            if error and not navigation_unchanged:
                # Verify a usable home before trying another chapter. Do not
                # repeatedly feed input into an unchanged unknown screen.
                workflow.update('navigation',message=f'{prefix} 已保存，正在确认返回主页后继续其他章节')
                nav.home()
        summary=persist('finished' if summary['complete'] else 'partial');export_coverage()
        message=(f'全目录扫描完成：确认 {summary["confirmed"]} 项，未通关或未满星 {summary["pending"]} 项'
                 if summary['complete'] else
                 f'本轮扫描结束：已确认 {summary["confirmed"]}/{summary["total"]} 项，剩余 {summary["unseen"]} 项待确认；另有 {summary["unsupported"]} 项其他难度待适配。可继续扫描，记录已保存')
        return {'state':'done' if summary['complete'] else 'partial','message':message}
    except BaseException as exc:
        persist('stopped' if isinstance(exc,InterruptedError) else 'error');export_coverage();raise
    finally:nav.on_scan_stage=old_callback

