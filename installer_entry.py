"""Windows executable entry point, including isolated worker processes."""
import json
import sys
import traceback


def pick_directory():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    root.update()
    try:
        print(filedialog.askdirectory(title='选择战国兰斯游戏目录（含 rance7GA.ald / rance7GM.ald）') or '')
    finally:
        root.destroy()


def self_check():
    import hashlib
    import subprocess
    import tkinter
    from PIL import Image
    from runtime_paths import app_root
    import patch_engine
    base = app_root()
    roster = patch_engine.load_roster(base)
    checked = set()
    for row in roster['entries']:
        for key in ('after_file', 'after_preview', 'after_thumb'):
            name = row.get(key)
            if not name or name in checked:
                continue
            path = (base / name).resolve()
            if base not in path.parents:
                raise ValueError('Invalid asset path: ' + name)
            with Image.open(path) as img:
                img.verify()
            checked.add(name)
        if hashlib.sha256((base / row['after_file']).read_bytes()).hexdigest() != row['after_sha256']:
            raise ValueError('Asset hash mismatch: ' + row['id'])
    alice = subprocess.run([str(base / 'tools/alice.exe'), '--version'],
                           capture_output=True, check=True, text=True)
    root = tkinter.Tk()
    root.withdraw()
    tk_version = root.tk.eval('info patchlevel')
    root.destroy()
    print(json.dumps(dict(passed=True, characters=len(roster['characters']),
                          entries=len(roster['entries']), assets=len(checked),
                          tcl=tk_version,
                          alice=alice.stdout.strip()), ensure_ascii=False))


def main():
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf8', errors='replace')
    if sys.stderr:
        sys.stderr.reconfigure(encoding='utf8', errors='replace')
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode in ('--pick-directory', '--resource-tool', '--engine', '--self-check'):
        del sys.argv[1]
        if mode == '--pick-directory':
            return pick_directory()
        if mode == '--resource-tool':
            import resource_tool
            return resource_tool.main()
        if mode == '--engine':
            import patch_engine
            return patch_engine.main()
        return self_check()
    import web_server
    return web_server.main()


if __name__ == '__main__':
    interactive = len(sys.argv) == 1
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        if getattr(sys, 'frozen', False) and interactive:
            input('\n启动失败，请保存上方错误信息后按回车退出。')
        sys.exit(1)
