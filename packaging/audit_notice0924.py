"""Prove the scan-failure banner offers a resume shortcut, in an isolated sandbox.

The sandbox points `device` at an offline serial, so the worker fails while
opening the device: no ADB input, no game interaction, no contact with the live
emulator or the live server (unique port and root).
"""
import json, os, re, socket, subprocess, sys, time, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'App-0.9.24' / 'MAA自动推图助手.exe'
EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, path):
    with OPENER.open(url + path, timeout=8) as response:
        return json.load(response)


def main():
    sandbox = ROOT / '_tmp' / ('notice0924-' + uuid.uuid4().hex)
    project = sandbox / 'AutoStory'
    (project / 'web').mkdir(parents=True)
    (project / 'web/index.html').write_bytes((ROOT / 'web/index.html').read_bytes())
    (project / 'runtime').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # An offline serial keeps Device() from touching the live emulator.
    (project / 'settings.json').write_text(json.dumps(
        {'port': port, 'device': '127.0.0.1:59999'}), encoding='utf-8')
    url = f'http://127.0.0.1:{port}'
    server = subprocess.Popen([str(EXE), '--server', '--root', str(project)],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=FLAGS)
    result = {'ok': False, 'sandbox': str(sandbox), 'url': url}
    shot = ROOT / 'runtime' / 'notice0924-banner.png'
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                health = get(url, '/api/health')
                break
            except OSError:
                time.sleep(.3)
        else:
            raise RuntimeError('sandbox server did not answer')
        result['health'] = health
        html = OPENER.open(url + '/', timeout=8).read().decode('utf-8')
        token = re.search("const token='([^']+)'", html).group(1)
        request = urllib.request.Request(url + '/api/action', data=b'{"action":"scan-all","chapter":"ALL"}',
                                         headers={'X-AutoStory-Token': token, 'Content-Type': 'application/json'})
        result['action'] = json.load(OPENER.open(request, timeout=10))
        deadline = time.monotonic() + 60
        state = None
        while time.monotonic() < deadline:
            state = get(url, '/api/state')
            if not state.get('running') and state['status'].get('state') in ('error', 'stopped'):
                break
            time.sleep(.4)
        result['status'] = state['status']
        if state['status'].get('state') != 'error':
            raise RuntimeError('expected a device failure, got %r' % (state['status'],))
        if state['status'].get('action') != 'scan-all':
            raise RuntimeError('unexpected action %r' % (state['status'].get('action'),))
        # Render the real page through a real browser and keep the screenshot.
        subprocess.run([str(EDGE), '--headless=new', '--disable-gpu', '--hide-scrollbars',
                        '--window-size=1600,1000', '--screenshot=' + str(shot), url + '/'],
                       check=True, timeout=90, creationflags=FLAGS,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result['banner_module_present'] = 'id="noticeAction"' in (ROOT / 'web/index.html').read_text('utf-8')
        result['screenshot'] = str(shot)
        result['screenshot_bytes'] = shot.stat().st_size
        result['ok'] = True
    finally:
        try:
            get(url, '/api/health')
        except OSError:
            pass
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        result['at'] = time.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        (ROOT / 'runtime/notice0924-live-proof.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'health'}, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
