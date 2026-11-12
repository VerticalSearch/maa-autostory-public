from __future__ import annotations
import subprocess
import time
from pathlib import Path
import cv2
import numpy as np
from .common import MAA, DATA, check_stop, assert_gui_idle, read_json, settings, log, DeviceUnavailableError
from .adb_transport import run_command
from .raw_capture import decode_raw_screencap, RawCaptureError

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

class Device:
    def __init__(self, *, connect_timeout=20):
        options = settings()
        c = {}
        if not options.get('adb_path') or not options.get('device'):
            cfg = read_json(MAA / 'config/gui.new.json', {})
            if isinstance(cfg, dict):
                configs = cfg.get('Configurations', {})
                current = cfg.get('Current')
                profile = configs.get(current, {}) if isinstance(configs, dict) and isinstance(current, str) else {}
                gui = profile.get('Gui', {}) if isinstance(profile, dict) else {}
                candidate = gui.get('ConnectSettings', {}) if isinstance(gui, dict) else {}
                if isinstance(candidate, dict):c = candidate
        self.adb = options.get('adb_path') or c.get('AdbPath')
        if not isinstance(self.adb, str) or not self.adb.strip():
            raise RuntimeError('尚未配置 ADB：请在 settings.json 填写 adb_path，或先在 MAA 中配置模拟器连接')
        if not Path(self.adb).exists():
            raise RuntimeError(f'ADB 不存在：{self.adb}')
        preferred = options.get('device') or c.get('Address') or ''
        if not isinstance(preferred, str):raise ValueError('模拟器 device 必须是设备序列号或连接地址')
        devices = self.raw('devices',timeout=connect_timeout).decode('utf-8', 'replace')
        online = [l.split()[0] for l in devices.splitlines() if l.endswith('\tdevice')]
        if preferred not in online and ':' in preferred:
            self.raw('connect', preferred,timeout=connect_timeout)
            online = [l.split()[0] for l in self.raw('devices',timeout=connect_timeout).decode().splitlines() if l.endswith('\tdevice')]
        if preferred in online:
            self.serial = preferred
        elif not preferred and len(online) == 1:
            self.serial = online[0]
        else:
            if preferred:raise DeviceUnavailableError(f'指定模拟器 {preferred} 不在线；在线设备：{online}。请核对 device，未切换到其他设备')
            raise RuntimeError(f'无法唯一确定模拟器，在线设备：{online}')
        self.size = (1280, 720)
        transport = options.get('capture_transport', 'auto')
        if transport not in ('auto', 'raw', 'png'):
            raise ValueError('capture_transport must be auto, raw or png')
        # Raw pixels trade bandwidth for lower emulator-side compression cost.
        # Keep PNG on remote/USB devices unless explicitly requested.
        local = self.serial.startswith(('emulator-', '127.0.0.1:', 'localhost:', '[::1]:'))
        self._capture_transport = 'raw' if transport == 'raw' or (transport == 'auto' and local) else 'png'
        self._capture_fallback = None

    def raw(self, *args, timeout=20):
        try:
            p = run_command([self.adb, *map(str, args)], check_stop=check_stop,
                            timeout=timeout, creationflags=NO_WINDOW)
        except InterruptedError:
            raise
        except (subprocess.TimeoutExpired,OSError) as e:
            raise DeviceUnavailableError('模拟器连接或响应异常，已停止本轮操作：'+str(e)) from e
        if p.returncode:
            stderr=p.stderr.decode('utf-8', 'replace').strip()
            error=DeviceUnavailableError('模拟器操作失败，已停止本轮操作：'+stderr)
            error.adb_stderr=stderr;error.adb_returncode=p.returncode
            raise error
        return p.stdout

    def ready(self, timeout=4):
        """Read-only readiness probe used by bounded recovery.

        Never sends input, never switches device and never restarts the ADB
        server: it only asks the same serial for its transport state.
        """
        try:
            return self.raw('-s',self.serial,'get-state',timeout=timeout).strip()==b'device'
        except InterruptedError:
            raise
        except DeviceUnavailableError:
            return False

    def _capture_bytes(self, *, png=False, timeout=20):
        """Retry one closed screenshot stream only after verifying the same device.

        Input commands never use this path: a failed click may already have run.
        A real offline/unauthorized device and user stop still terminate the task.
        """
        command=['-s',self.serial,'exec-out','screencap']+(['-p'] if png else [])
        try:
            return self.raw(*command,timeout=timeout)
        except DeviceUnavailableError as error:
            if getattr(error,'adb_stderr',None)!='error: closed':raise
            check_stop()
            self.invalidate_frame(getattr(self,'_settle_rois',()),kind=getattr(self,'_settle_kind','fixed'))
            log('截图连接短暂关闭，正在核对同一模拟器并重取一次截图')
            state=self.raw('-s',self.serial,'get-state',timeout=4).strip()
            if state!=b'device':
                raise DeviceUnavailableError('截图恢复时模拟器未就绪，进度已保存：'+state.decode('utf-8','replace')) from error
            STOP_WAIT(.2);check_stop()
            data=self.raw(*command,timeout=timeout)
            self._capture_recovery_generation=getattr(self,'_capture_recovery_generation',0)+1
            log('截图连接已恢复，继续检查当前画面')
            return data

    def shot(self, save=None):
        im = self._capture()
        if save:
            Path(save).parent.mkdir(parents=True, exist_ok=True)
            cv2.imencode('.png', im)[1].tofile(str(save))
        return im

    def invalidate_frame(self, rois=(),kind='fixed'):
        """Any input, including MaaCore tasks, invalidates settled-state evidence."""
        self._needs_settle=True
        self._settle_rois=tuple(rois)
        self._settle_kind=kind

    def stable_shot(self, save=None):
        # Navigation consumes this method; battle/result frame capture stays raw.
        from .motion import settle, UnsettledFrameError
        if not getattr(self, '_needs_settle', True):
            return self.shot(save)
        original_transport=getattr(self,'_capture_transport','png')
        recovery_generation=getattr(self,'_capture_recovery_generation',0)
        im, report = settle(self._capture, check_stop, STOP_WAIT, rois=getattr(self,'_settle_rois',()),kind=getattr(self,'_settle_kind','fixed'))
        recovered=getattr(self,'_capture_recovery_generation',0)!=recovery_generation
        if not report['confirmed'] and (recovered or (original_transport=='raw' and self._capture_transport=='png')):
            # A recovered capture timeout may have consumed the settling window.
            # Allow one fresh window on the new transport, with no input replay.
            check_stop()
            im, report = settle(self._capture, check_stop, STOP_WAIT, rois=getattr(self,'_settle_rois',()),kind=getattr(self,'_settle_kind','fixed'))
        self.last_settle = report
        if save:
            Path(save).parent.mkdir(parents=True, exist_ok=True)
            cv2.imencode('.png', im)[1].tofile(str(save))
        if not report['confirmed']:
            from .common import write_json
            target = DATA/'screens'/'unstable'/f'{time.time_ns()}.png'
            target.parent.mkdir(parents=True, exist_ok=True)
            cv2.imencode('.png', im)[1].tofile(str(target))
            write_json(target.with_suffix('.json'), report)
            raise UnsettledFrameError('画面仍在运动或加载，停止导航并保留截图')
        self._needs_settle = False
        return im

    def _capture(self):
        check_stop()
        started = time.monotonic()
        transport = getattr(self, '_capture_transport', 'png')
        if transport == 'raw':
            try:
                data = self._capture_bytes()
                # Pin the modern header; guessing by length can accept a truncated
                # 16-byte frame as a legacy 12-byte frame and shift every pixel.
                im = decode_raw_screencap(data, header_bytes=16)
            except DeviceUnavailableError as error:
                if not isinstance(error.__cause__,subprocess.TimeoutExpired):raise
                check_stop()
                self._capture_transport = transport = 'png'
                self._capture_fallback = '原始截图超时，改用 PNG'
                log('原始截图超时，尝试一次 PNG 截图恢复；不重放点击或滑动')
            except RawCaptureError as error:
                self._capture_transport = transport = 'png'
                self._capture_fallback = str(error)
                log('原始截图格式未通过校验，本次连接改用 PNG：' + str(error))
                check_stop()
        if transport == 'png':
            data = self._capture_bytes(png=True,timeout=8)
            try:
                im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
            except cv2.error:
                im = None
            if im is None:
                raise DeviceUnavailableError('模拟器截图为空或损坏，已停止本轮操作')
        h, w = im.shape[:2]
        if abs(w / h - 16 / 9) > .08:
            raise RuntimeError(f'当前游戏画面 {w}×{h}，首版要求 16:9')
        self.size = (w, h)
        if (w,h) != (1280,720):
            im = cv2.resize(im, (1280, 720))
        check_stop()
        self.last_capture = {'transport': transport, 'bytes': len(data), 'size': [w,h],
            'seconds': time.monotonic()-started, 'fallback': getattr(self,'_capture_fallback',None)}
        return im

    # Blind post-input pauses only give the game time to *start* reacting.
    # Every following stable_shot() still requires consecutive settled frames,
    # so these are deliberately short (0.9.28: .6/.7/.6 -> .45/.55/.45).
    TAP_WAIT=.45
    SWIPE_WAIT=.55
    BACK_WAIT=.45

    def tap(self, x, y, wait=None):
        check_stop(); assert_gui_idle()
        w,h = self.size
        self.invalidate_frame()
        self.raw('-s', self.serial, 'shell', 'input', 'tap', round(x*w/1280), round(y*h/720))
        if STOP_WAIT(self.TAP_WAIT if wait is None else wait): check_stop()

    def swipe(self, x1,y1,x2,y2, duration=450):
        check_stop(); assert_gui_idle()
        w,h=self.size
        self.invalidate_frame()
        self.raw('-s',self.serial,'shell','input','swipe',round(x1*w/1280),round(y1*h/720),round(x2*w/1280),round(y2*h/720),duration)
        STOP_WAIT(self.SWIPE_WAIT)

    def back(self):
        check_stop(); assert_gui_idle()
        self.invalidate_frame()
        self.raw('-s',self.serial,'shell','input','keyevent',4)
        STOP_WAIT(self.BACK_WAIT)

def STOP_WAIT(seconds):
    from .common import STOP
    deadline = time.monotonic() + max(0,seconds)
    while True:
        check_stop()
        remaining = deadline - time.monotonic()
        if remaining <= 0:return False
        STOP.wait(min(.1,remaining))


# Compatibility export for existing callers and frozen entrypoints.
from .vision import Vision
