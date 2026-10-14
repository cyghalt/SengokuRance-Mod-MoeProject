"""Build a portable Windows ZIP from the public release files.

Build environment: Python 3.12, Pillow 12.2.0, PyInstaller 6.22.0.
python -m pip install Pillow==12.2.0 PyInstaller==6.22.0
python build_windows_installer.py --alice-dir PATH --alice-source PATH --libiconv-source PATH
alice-source must be the 0.13.0 checkout including its libsys4 submodule.
libiconv-source is the official libiconv-1.17.tar.gz source archive.
"""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILES = ['README.md', 'LICENSE', 'ASSET_LICENSE.md', 'THIRD_PARTY_NOTICES.md',
         'requirements.txt', '启动安装器.bat', 'patch_engine.py', 'web_server.py',
         'content_tools.py', 'release_catalog.py', 'release_docs.py', 'resource_tool.py',
         'runtime_paths.py', 'installer_entry.py', 'factions.json', 'release_scope.json',
         'tools/README.md', 'build_windows_installer.py', 'third-party-licenses/Tcl-LICENSE.txt']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alice-dir', required=True, type=Path)
    parser.add_argument('--alice-source', required=True, type=Path)
    parser.add_argument('--libiconv-source', required=True, type=Path)
    parser.add_argument('--out', type=Path, default=BASE / 'build/windows-installer')
    parser.add_argument('--version', default='2026.10.14')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('Build on 64-bit Windows with 64-bit Python')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    alice = args.alice_dir.resolve()
    source = args.alice_source.resolve()
    libiconv = args.libiconv_source.resolve()
    with tarfile.open(libiconv) as source_archive:
        readme = source_archive.extractfile('libiconv-1.17/README').read()
    if readme.replace(b'\r\n', b'\n') != (alice / 'licenses/libiconv/README').read_bytes().replace(b'\r\n', b'\n'):
        raise ValueError('libiconv source does not match the README shipped with alice-tools')
    if not (alice / 'alice.exe').is_file() or not (alice / 'COPYING.txt').is_file():
        raise ValueError('Use the extracted official alice-tools 0.13.0 release')
    if not (source / 'subprojects/libsys4/meson.build').is_file():
        raise ValueError('Corresponding source must include the libsys4 submodule')
    version = subprocess.check_output([str(alice / 'alice.exe'), '--version'], text=True).strip()
    if '0.13.0' not in version:
        raise ValueError('Expected alice-tools 0.13.0: ' + version)
    source_commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if source_commit != 'a6ac8ecc8149009876cc9030a7eced5bebe3e8a0':
        raise ValueError('Expected the official alice-tools 0.13.0 source commit')
    subprocess.run(['git', '-C', str(source), 'diff', '--exit-code'], check=True)
    submodule = subprocess.check_output(['git', '-C', str(source), 'submodule', 'status'], text=True)
    if not submodule.startswith(' 918fc7d0c1d35cdfc3c2a694bb14e5971a8df95b'):
        raise ValueError('Unexpected libsys4 source revision')
    roster = json.loads((BASE / 'data/roster.json').read_text('utf8'))
    names = set(FILES)
    for row in roster['entries']:
        for key in ('after_file', 'after_preview', 'after_thumb'):
            name = row[key]
            path = (BASE / name).resolve()
            if BASE not in path.parents or not path.is_file():
                raise ValueError('Missing or invalid asset: ' + name)
            names.add(name)
        if hashlib.sha256((BASE / row['after_file']).read_bytes()).hexdigest() != row['after_sha256']:
            raise ValueError('Asset hash mismatch: ' + row['id'])
        row.update(before_preview=None, before_thumb=None)
    roster.update(game_default='', unique_before=0)
    for folder, extensions in [('web', {'.js', '.css', '.html'}), ('docs', {'.md', '.csv', '.json'})]:
        names.update(p.relative_to(BASE).as_posix() for p in (BASE / folder).rglob('*')
                     if p.is_file() and p.suffix in extensions)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
                    '--console', '--name', 'SengokuRanceInstaller', '--distpath', str(out / 'dist'),
                    '--workpath', str(out / 'pyinstaller-work'), '--specpath', str(out),
                    str(BASE / 'installer_entry.py')], check=True)
    app = out / 'dist/SengokuRanceInstaller'
    for name in sorted(names):
        dest = app / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / name, dest)
    (app / 'data/roster.json').write_text(json.dumps(roster, ensure_ascii=False, indent=1), 'utf8')
    shutil.copy2(alice / 'alice.exe', app / 'tools/alice.exe')
    shutil.copy2(alice / 'COPYING.txt', app / 'tools/COPYING-alice-tools.txt')
    shutil.copytree(alice / 'licenses', app / 'tools/licenses', dirs_exist_ok=True)
    shutil.copy2(alice / 'README.md', app / 'tools/alice-tools-README.md')
    licenses = app / 'third-party'
    licenses.mkdir(exist_ok=True)
    shutil.copy2(libiconv, licenses / 'libiconv-1.17.tar.gz')
    with zipfile.ZipFile(licenses / 'alice-tools-0.13.0-source.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source.rglob('*')):
            if path.is_file() and '.git' not in path.relative_to(source).parts:
                bundle.write(path, 'alice-tools-0.13.0/' + path.relative_to(source).as_posix())
    import PIL
    import PyInstaller
    python_base = Path(sys.base_prefix)
    shutil.copy2(python_base / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    pillow_dist = importlib.metadata.distribution('Pillow')
    for item in pillow_dist.files:
        if '/licenses/' in item.as_posix() and item.name.lower().startswith('license'):
            shutil.copy2(pillow_dist.locate_file(item), licenses / ('Pillow-' + item.name))
    pyinstaller_dist = importlib.metadata.distribution('PyInstaller')
    for item in pyinstaller_dist.files:
        if item.as_posix().endswith('/licenses/COPYING.txt'):
            shutil.copy2(pyinstaller_dist.locate_file(item), licenses / 'PyInstaller-COPYING.txt')
    shutil.copy2(BASE / 'third-party-licenses/Tcl-LICENSE.txt', licenses / 'Tcl-LICENSE.txt')
    build_info = dict(version=args.version, python=platform.python_version(), pillow=PIL.__version__,
                      pyinstaller=PyInstaller.__version__, alice_tools=version,
                      alice_source_commit=source_commit, libsys4_commit=submodule.split()[0],
                      characters=len(roster['characters']), entries=len(roster['entries']))
    (app / 'BUILD_INFO.json').write_text(json.dumps(build_info, indent=2, ensure_ascii=False), 'utf8')
    (app / '先读我.txt').write_text(
        '战国兰斯 · 娘化计划 Windows 64 位便携安装器\n\n'
        '1. 完整解压 ZIP 到普通可写文件夹，不要在压缩包里直接运行。\n'
        '2. 双击 SengokuRanceInstaller.exe（也可双击 启动安装器.bat）。\n'
        '3. 浏览器打开后，选择自己的游戏目录，检测状态，再选择角色安装。\n'
        '无需安装 Python、Pillow 或自行下载 alice-tools，无需 AI 服务。\n'
        '安装前退出游戏。安装器窗口需保持打开；使用结束后关闭窗口即可停止服务。\n'
        '备份和设置保存在此文件夹的 build/ 和 安装器设置.json，请保留以便恢复。\n'
        '目前支持已测试的 Windows Steam 版，需要 rance7GA.ald 和 rance7GM.ald。\n'
        '第三方许可证见 third-party/ 和 tools/，alice-tools 对应源码随包提供。\n'
        'CY 发布：https://github.com/cyghalt/SengokuRance-Mod-MoeProject\n', 'utf-8-sig')
    subprocess.run([str(app / 'SengokuRanceInstaller.exe'), '--self-check'], check=True)
    archive = out / f'SengokuRance-Moe-Installer-{args.version}-win64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                bundle.write(path, 'SengokuRanceInstaller/' + path.relative_to(app).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n', 'ascii')
    print(json.dumps(dict(archive=str(archive), bytes=archive.stat().st_size, sha256=digest), indent=2))


if __name__ == '__main__':
    main()
