"""发布版网页安装器：在本机 127.0.0.1 起一个本地服务，用浏览器承载界面。

只绑定本机回环地址，不对外网开放；所有写盘动作都通过 patch_engine 完成。
服务端只提供名单、检测、浏览目录、安装/恢复与进度查询等接口，页面在 web/ 下。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from runtime_paths import app_root, child_command

BASE = app_root()
sys.path.insert(0, str(BASE))
import patch_engine as engine  # noqa: E402
import content_tools
from release_catalog import with_factions

SETTINGS = BASE / '安装器设置.json'
ROSTER = engine.load_roster(BASE)
SETTINGS_LOCK = threading.Lock()
JOBS: dict[str, 'Job'] = {}
JOBS_LOCK = threading.Lock()
JOB_SEQ = [0]

BROWSE_SCRIPT = r'''
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.update()
path = filedialog.askdirectory(title="选择战国兰斯游戏目录（含 rance7GA.ald / rance7GM.ald）")
root.destroy()
print(path or "")
'''


class Job:
    """后台安装/恢复任务的进度与日志容器。"""

    def __init__(self, kind):
        self.kind = kind
        self.lock = threading.Lock()
        self.frac = 0.0
        self.message = '准备中……'
        self.log = []
        self.state = 'running'
        self.result = None
        self.error = None

    def progress(self, fraction, message):
        with self.lock:
            self.frac = max(0.0, min(1.0, float(fraction)))
            self.message = message

    def logmsg(self, message):
        with self.lock:
            self.log.append(message)

    def finish(self, result):
        with self.lock:
            self.state = 'done'
            self.frac = 1.0
            self.result = result

    def fail(self, error):
        with self.lock:
            self.state = 'error'
            self.error = str(error)

    def snapshot(self):
        with self.lock:
            return dict(kind=self.kind, state=self.state, frac=self.frac, message=self.message,
                        log=self.log[-300:], result=self.result, error=self.error)


def load_settings():
    with SETTINGS_LOCK:
        if SETTINGS.is_file():
            try:
                return json.loads(SETTINGS.read_text('utf8'))
            except Exception:
                pass
    return {}


def save_settings(data):
    with SETTINGS_LOCK:
        try:
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
        except Exception:
            pass


def new_job(kind, target):
    with JOBS_LOCK:
        if any(j.state == 'running' for j in JOBS.values()):
            raise ValueError('已有任务正在执行，请等待完成。')
        JOB_SEQ[0] += 1
        job = Job(kind)
        JOBS[str(JOB_SEQ[0])] = job
        for key in list(JOBS)[:-8]:  # 只保留最近的任务
            JOBS.pop(key, None)
    thread = threading.Thread(target=target, args=(job,), daemon=True)
    thread.start()
    return str(JOB_SEQ[0]), job


def run_install(job, game, ids):
    try:
        report = engine.install(ROSTER, game, set(ids), do_install=True,
                                progress=job.progress, log=job.logmsg)
    except Exception as exc:
        job.fail(exc)
        return
    job.finish({k: report.get(k) for k in ('passed', 'installed', 'source_positions',
                                           'backup_directory', 'work', 'installed_at')})


def run_restore(job, game, backup):
    try:
        result = engine.restore(game, backup, progress=job.progress, log=job.logmsg)
    except Exception as exc:
        job.fail(exc)
        return
    job.finish(result)


def choose_directory():
    """在独立进程里弹原生文件夹选择框，避免 Tk 线程问题。"""
    try:
        proc = subprocess.run(child_command('--pick-directory'),
                              capture_output=True, text=True, timeout=600)
    except Exception as exc:
        return dict(error=f'无法打开目录选择框：{exc}')
    if proc.returncode:
        return dict(error=(proc.stderr or '目录选择框异常退出').strip()[:400])
    return dict(path=(proc.stdout or '').strip())


def last_backup():
    marker = BASE / 'build' / 'last-backup.json'
    if not marker.is_file():
        return None
    try:
        info = json.loads(marker.read_text('utf8'))
    except Exception:
        return None
    backup = Path(info.get('backup', ''))
    if not backup.is_dir():
        return None
    return dict(backup=str(backup), game=info.get('game', ''),
                archives=info.get('archives', []), at=info.get('at', ''))


class Handler(BaseHTTPRequestHandler):
    server_version = 'RanceMoeInstaller/1.0'

    def log_message(self, fmt, *args):  # 安静一点，只把错误写到 stderr
        pass

    # ---------------------------------------------------------- 基础响应

    def send_bytes(self, data, ctype, status=200):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode('utf8'),
                        'application/json; charset=utf-8', status)

    def read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 8_000_000:
            raise ValueError('请求大小无效')
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError('请求必须是 JSON 对象')
        return data

    def same_origin(self):
        origin = self.headers.get('Origin')
        return not origin or origin == 'http://' + self.headers.get('Host', '')

    # ---------------------------------------------------------- 路由

    def do_GET(self):
        url = urlparse(self.path)
        args = parse_qs(url.query)
        one = lambda key, default='': args.get(key, [default])[0]
        try:
            if url.path == '/':
                return self.serve_file(BASE / 'web' / 'index.html')
            if url.path.startswith('/data/') or url.path.startswith('/web/'):
                return self.serve_file(BASE / unquote(url.path.lstrip('/')))
            if url.path == '/api/roster':
                return self.send_json(dict(roster=with_factions(ROSTER, BASE), settings=load_settings(),
                                           backup=last_backup(), originals_available=bool(content_tools.extracted()), package_index='/build/packages/势力包/index.html'
                                           if (BASE / 'build/packages/势力包/index.html').is_file() else None))
            if url.path == '/api/local-preview':
                entry = next((e for e in ROSTER['entries'] if e['id'] == one('id')), None)
                if not entry:
                    return self.send_json({'error': '未知资源'}, 404)
                return self.send_bytes(content_tools.local_preview(entry), 'image/jpeg')
            if url.path == '/api/search':
                return self.send_json(content_tools.search(one('q'), one('kind', 'all'), int(one('offset', '0'))))
            if url.path == '/api/resources':
                return self.send_json(content_tools.resources(one('q'), int(one('offset', '0'))))
            if url.path.startswith('/extracted/'):
                root = content_tools.extracted()
                if not root:
                    return self.send_json({'error': '请先解压资源'}, 404)
                target = (root / unquote(url.path[len('/extracted/'):])).resolve()
                if root not in target.parents:
                    return self.send_json({'error': '路径越界'}, 403)
                return self.serve_file(target)
            if url.path.startswith('/build/packages/'):
                return self.serve_file(BASE / unquote(url.path.lstrip('/')))
            if url.path == '/api/detect':
                return self.send_json(engine.detect_game(one('path')))
            if url.path == '/api/state':
                path = one('path')
                if not engine.detect_game(path)['ok']:
                    return self.send_json({'error': '请先选择正确的游戏目录。'}, 400)
                return self.send_json(dict(states=engine.inspect_game(ROSTER, path)))
            if url.path == '/api/browse':
                return self.send_json(choose_directory())
            if url.path == '/api/job':
                with JOBS_LOCK:
                    job = JOBS.get(one('id'))
                return self.send_json(job.snapshot() if job else {'error': '任务不存在'})
            if url.path == '/api/quit':
                self.send_json({'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self.send_json({'error': '接口不存在'}, 404)
        except (engine.PatchError, ValueError) as error:
            return self.send_json({'error': str(error)}, 400)
        except Exception as error:  # noqa: BLE001
            return self.send_json({'error': f'服务器错误：{error}'}, 500)

    def do_POST(self):
        if not self.same_origin():
            return self.send_json({'error': '不允许跨来源操作'}, 403)
        url = urlparse(self.path)
        try:
            data = self.read_json()
            if url.path == '/api/settings':
                save_settings({'game': str(data.get('game', ''))})
                return self.send_json({'ok': True})
            if url.path == '/api/extract':
                game = str(data.get('game', '')).strip().strip('"')
                if not engine.detect_game(game)['ok'] or not (Path(game) / 'rance7.ain').is_file():
                    return self.send_json({'error': '请选择包含 ALD 和 rance7.ain 的游戏目录'}, 400)
                job_id, _ = new_job('extract', lambda job: content_tools.run_extract(job, game))
                return self.send_json({'job': job_id})
            if url.path == '/api/package':
                ids = data.get('ids', [])
                if not isinstance(ids, list) or not ids or not set(ids) <= {e['id'] for e in ROSTER['entries']}:
                    raise ValueError('请选择有效角色或势力')
                job_id, _ = new_job('package', lambda job: content_tools.run_package(job, ROSTER, ids))
                return self.send_json({'job': job_id})
            if url.path == '/api/install':
                game = str(data.get('game', '')).strip().strip('"')
                if not engine.detect_game(game)['ok']:
                    return self.send_json({'error': '所选目录不是战国兰斯游戏目录。'}, 400)
                if not Path(engine.ALICE).is_file():
                    return self.send_json({'error': '缺少 tools/alice.exe。'}, 400)
                ids = data.get('ids')
                ids = [e['id'] for e in ROSTER['entries']] if ids == 'all' else list(ids or [])
                if not ids:
                    return self.send_json({'error': '没有选中任何资源。'}, 400)
                save_settings({'game': game})
                job_id, _ = new_job('install', lambda job: run_install(job, game, ids))
                return self.send_json({'job': job_id, 'count': len(ids)})
            if url.path == '/api/restore':
                info = last_backup()
                if not info:
                    return self.send_json({'error': '没有可用的备份记录。'}, 400)
                game = str(data.get('game', '')).strip().strip('"') or info['game']
                if not Path(game).is_dir():
                    return self.send_json({'error': '游戏目录不存在：' + game}, 400)
                job_id, _ = new_job('restore',
                                    lambda job: run_restore(job, game, info['backup']))
                return self.send_json({'job': job_id, 'backup': info['backup']})
            return self.send_json({'error': '接口不存在'}, 404)
        except (ValueError, json.JSONDecodeError) as error:
            return self.send_json({'error': str(error)}, 400)
        except Exception as error:  # noqa: BLE001
            return self.send_json({'error': f'服务器错误：{error}'}, 500)

    # ---------------------------------------------------------- 静态文件

    def serve_file(self, path):
        target = path.resolve()
        if BASE not in target.parents and target != BASE:
            return self.send_json({'error': '路径越界'}, 403)
        if not target.is_file():
            return self.send_json({'error': '文件不存在'}, 404)
        ctype = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
            ctype += '; charset=utf-8'
        self.send_bytes(target.read_bytes(), ctype)


def pick_port(preferred, tries=20):
    import socket
    for port in range(preferred, preferred + tries):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                continue
        return port
    return preferred


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8898)
    parser.add_argument('--open', action='store_true', help='启动后打开浏览器')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)

    port = pick_port(args.port)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{port}/'
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print(f'战国兰斯·娘化计划 安装器（网页版）：{url}', flush=True)
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
