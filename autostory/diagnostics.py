"""Bounded connection/frame checks; never construct Core or navigate the game."""
import time
import cv2
import numpy as np
from .common import DATA,MAA,read_json,write_json,now,check_stop,log


def error_advice(error):
    text=str(error)
    if 'ADB' in text or 'adb_path' in text:return '在 settings.json 设置有效的 adb_path，或先在 MAA 配置模拟器连接。'
    if '唯一' in text:return '在 settings.json 的 device 中填写目标模拟器的连接地址，避免选择错误设备。'
    if '16:9' in text:return '将模拟器调整为 16:9 分辨率后重新检测。'
    if '截图' in text:return '确认模拟器已启动并显示游戏，重新连接 ADB 后再检测。'
    return '确认模拟器已启动、ADB 调试可用且连接地址正确，然后重新检测。'


def run_diagnostics(device_factory=None):
    if device_factory is None:
        from .device import Device
        device_factory=Device
    started=time.monotonic()
    report={'at':now(),'run_id':read_json(DATA/'status.json',{}).get('run_id'),
            'state':'running','summary':'正在检查设备连接与当前截图','checks':[]}
    target=DATA/'diagnostics.json'
    write_json(target,report)
    def check(name,state,message):report['checks'].append({'name':name,'state':state,'message':message})
    try:
        check_stop()
        d=device_factory(connect_timeout=5)
        check('设备连接','ok','已连接 '+d.serial)
        # One PNG capture, eight-second timeout, no raw fallback or input replay.
        raw=d.raw('-s',d.serial,'exec-out','screencap','-p',timeout=8)
        check_stop()
        im=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR) if raw else None
        if im is None:raise RuntimeError('模拟器截图为空或无法解码')
        height,width=im.shape[:2]
        if abs(width/height-16/9)>.08:raise RuntimeError(f'截图 {width}×{height}，需要 16:9 分辨率')
        cv2.imencode('.png',im)[1].tofile(str(DATA/'diagnose.png'))
        blank=float(im.std())<2
        check('当前截图','warning' if blank else 'ok',
              f'{width}×{height}，画面接近纯色，请进入游戏后重试' if blank else f'{width}×{height}，截图可读取')
        missing=[name for name in ('MaaCore.dll','resource/battle_data.json',
                                  'resource/Arknights-Tile-Pos/overview.json') if not (MAA/name).is_file()]
        if not any((MAA/name).is_file() for name in ('resource/tasks/tasks.json','resource/tasks.json')):
            missing.append('resource/tasks/tasks.json 或 resource/tasks.json')
        check('MAA 资源','error' if missing else 'ok',
              '缺少 '+ '、'.join(missing)+'，请放回完整 MAA 目录后重试' if missing else '必要核心文件和地图资源存在')
        report['state']='error' if missing else ('warning' if blank else 'ok')
        report['summary']=('检测未通过：请修复缺失的 MAA 资源' if missing else
                           '连接正常，但画面接近纯色，请进入游戏后重试' if blank else
                           '连接与截图正常；请保持游戏已登录，推图时会重新核查干员和关卡')
        # This checks file presence and pixels, not a successful MaaCore load,
        # login state, network reachability, or battle readiness.
    except InterruptedError:
        report.update(state='stopped',summary='检测已停止')
        raise
    except Exception as exc:
        report.update(state='error',summary=str(exc),advice=error_advice(exc))
        check('检测错误','error',str(exc)+'；'+report['advice'])
        raise RuntimeError(report['summary']+'；'+report['advice']) from exc
    finally:
        report.update(at=now(),seconds=round(time.monotonic()-started,2))
        write_json(target,report);log(report['summary'])
    if report['state']=='error':raise RuntimeError(report['summary'])
    return report
