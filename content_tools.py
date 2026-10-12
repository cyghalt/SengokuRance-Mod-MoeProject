"""独立发布版的资源解压、剧本搜索和分包导出。"""
import datetime
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from release_catalog import with_factions

BASE = Path(__file__).resolve().parent


def extracted():
    marker = BASE / 'build/extracted.json'
    if not marker.is_file():
        return None
    root = Path(json.loads(marker.read_text('utf8'))['workspace']).resolve()
    if BASE / 'build' not in root.parents:
        raise ValueError('解压工作区路径无效')
    return root


def run_extract(job, game):
    try:
        if not (BASE / 'tools/alice.exe').is_file():
            raise ValueError('缺少 tools/alice.exe，请按 tools/README.md 从上游获取 alice-tools。')
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        root = BASE / 'build' / ('extracted-' + stamp)
        job.progress(0.05, '正在解压游戏资源与剧本……')
        with subprocess.Popen([sys.executable, str(BASE / 'resource_tool.py'), '--game', game,
                               '--workspace', str(root), 'extract'], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, encoding='utf8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                              env=__import__('os').environ | {'PYTHONIOENCODING': 'utf-8'}) as proc:
            for line in proc.stdout:
                job.logmsg(line.rstrip())
                hit = re.search(r'PNG (\d+) / (\d+)', line)
                if hit:
                    job.progress(0.1 + 0.85 * int(hit[1]) / int(hit[2]), line.strip())
            if proc.wait():
                raise ValueError('解压失败，详见日志；原游戏与已有工作区未修改。')
        marker = BASE / 'build/extracted.json'
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps({'workspace': str(root)}), 'utf8')
        temporary.replace(marker)
        job.finish({'workspace': str(root), 'message': '解压完成，可以浏览图片和搜索当前游戏剧本。'})
    except Exception as error:
        job.fail(error)


TABLE_CACHE = {}


def search(query, kind='all', offset=0, limit=20):
    if kind not in ('all', 'messages', 'strings') or len(query) > 200 or offset < 0 or not 1 <= limit <= 50:
        raise ValueError('搜索参数无效')
    active = extracted()
    if not active:
        raise ValueError('请先在“资源解压与浏览”中提取自己的游戏资源与剧本。')
    root = active
    results, total = [], 0
    for selected in ('messages', 'strings') if kind == 'all' else (kind,):
        folder = root / 'editable/text/steam_current' / selected
        files = sorted(folder.glob('*.json'))
        reference = folder.parent / 'by-function-reference.txt'
        stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files + ([reference] if reference.exists() else []))
        cached = TABLE_CACHE.get(str(folder))
        if cached is None or cached[0] != stamp:
            functions, function = {}, ''
            if reference.exists():
                for line in reference.read_text('utf8').splitlines():
                    hit = re.match(r'^;([ms])\[(\d+)\]', line)
                    if hit and hit[1] == selected[0]:
                        names = functions.setdefault(int(hit[2]), [])
                        if function and function not in names:
                            names.append(function)
                    elif line.startswith('; '):
                        function = line[2:]
            table = {}
            for p in files:
                for row in json.loads(p.read_text('utf8')):
                    table[row['id']] = dict(row, functions=functions.get(row['id'], []), file=str(p),
                                            url='/extracted/' + p.relative_to(root).as_posix())
            TABLE_CACHE[str(folder)] = (stamp, table)
        else:
            table = cached[1]
        if query.strip():
            for index in sorted(table):
                row = table[index]
                if query.casefold() not in row['text'].casefold():
                    continue
                if offset <= total < offset + limit:
                    context = [dict(table[i], match=i == index) for i in range(max(0, index - 2), index + 3) if i in table]
                    results.append(dict(row, kind=selected, context=context))
                total += 1
    return dict(total=total, results=results, offset=offset, limit=limit, source=str(root))


def resources(query='', offset=0, limit=24):
    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError('分页参数无效')
    root = extracted()
    if not root:
        return dict(total=0, results=[], source=None)
    manifest = json.loads((root / 'manifest.json').read_text('utf8'))
    rows = [r for r in manifest['images'] if query.casefold() in
            (' '.join([r['internal_name'], r['archive'], str(r['resource_ids']), f"{r['width']}x{r['height']}"])).casefold()]
    return dict(total=len(rows), source=str(root), results=[dict(r, url='/extracted/' + r['png']) for r in rows[offset:offset + limit]])


def local_preview(entry):
    """只从用户本机提取结果读取对照图，不将原版图片写入发布数据。"""
    root = extracted()
    if not root:
        raise ValueError('请先解压自己的游戏资源')
    manifest = json.loads((root / 'manifest.json').read_text('utf8'))
    row = next((r for r in manifest['images'] if
                r['archive'].casefold() == entry['archive'].casefold() and
                r['pointer_index'] == entry['pointer_index'] and
                r['internal_name'].casefold() == entry['internal_name'].casefold()), None)
    if not row:
        raise ValueError('本机版本中未找到此绑定资源')
    path = (root / row['png']).resolve()
    if root not in path.parents:
        raise ValueError('资源路径越界')
    import io
    from PIL import Image
    with Image.open(path) as opened:
        rgba = opened.convert('RGBA')
        rgba.thumbnail((512, 512))
        image = Image.new('RGB', rgba.size, 'white')
        image.paste(rgba, mask=rgba.getchannel('A'))
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=88)
        return output.getvalue()


def run_package(job, roster, ids):
    try:
        roster = with_factions(roster, BASE)
        ids = set(ids)
        entries = [e for e in roster['entries'] if e['id'] in ids]
        if not entries or len(entries) != len(ids):
            raise ValueError('分包选择为空或包含未知素材')
        entries = [dict(e, before_preview=None, before_thumb=None) for e in entries]
        subset = dict(roster, entries=entries, total_entries=len(entries), groups=[])
        for group in roster['groups']:
            indices = [i for i, entry in enumerate(entries) if entry['mod'] == group['mod']]
            if indices:
                subset['groups'].append(dict(group, entries=indices, count=len(indices)))
        from release_catalog import make_characters
        subset['characters'] = make_characters(entries, {e['character_id']: e['character_name'] for e in entries}, {})
        from release_docs import write_bindings
        import tempfile
        subset['unique_after'] = len({e['after_file'] for e in entries})
        subset['unique_before'] = len({e['before_preview'] for e in entries if e['before_preview']})
        subset['after_bytes'] = sum((BASE / p).stat().st_size for p in {e['after_file'] for e in entries})
        folder = BASE / 'build/packages'
        folder.mkdir(parents=True, exist_ok=True)
        selected_roles = {c['id'] for c in subset['characters']}
        complete = [f['name'] for f in roster['factions'] if f['characters'] and set(f['characters']) == selected_roles]
        label = complete[0] if complete else '选中角色'
        label = re.sub(r'[<>:"/\\|?*]', '-', label)
        name = label + '-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip'
        path = folder / name
        files = set()
        for e in entries:
            files.update(e[k] for k in ('after_file', 'after_preview', 'after_thumb') if e.get(k))
        files.update(p.relative_to(BASE).as_posix() for p in (BASE / 'web').rglob('*') if p.is_file())
        files.update(['patch_engine.py', 'web_server.py', 'content_tools.py', 'release_catalog.py',
                      'release_docs.py', 'release_scope.json', 'resource_tool.py', '启动安装器.bat', 'README.md'])
        # 第三方工具由使用者从上游获取；导出包也不携带游戏原图、剧本或工具二进制。
        for name in ('LICENSE', 'ASSET_LICENSE.md', 'THIRD_PARTY_NOTICES.md', 'requirements.txt'):
            if (BASE / name).is_file():
                files.add(name)
        files.update(p.relative_to(BASE).as_posix() for p in (BASE / 'tools').rglob('*.md'))
        factions = roster['factions']
        config = {'schema': 1, 'factions': [dict(id=f['id'], name=f['name'],
                     characters=[cid for cid in f['characters'] if cid in {c['id'] for c in subset['characters']}])
                     for f in factions if f['id'] != 'unassigned']}
        config['display_names'] = {c['id']: c['name'] for c in subset['characters']}
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('data/roster.json', json.dumps(subset, ensure_ascii=False))
            bundle.writestr('factions.json', json.dumps(config, ensure_ascii=False, indent=2))
            with tempfile.TemporaryDirectory() as temp:
                temp_root = Path(temp)
                (temp_root / 'factions.json').write_text(json.dumps(config, ensure_ascii=False), 'utf8')
                write_bindings(subset, temp_root)
                for file in (temp_root / 'docs').iterdir():
                    bundle.write(file, 'docs/' + file.name)
            for file in (BASE / 'docs').rglob('*.md'):
                if file.name != 'README.md':
                    bundle.write(file, file.relative_to(BASE).as_posix())
            for i, file in enumerate(sorted(files)):
                bundle.write(BASE / file, file)
                job.progress((i + 1) / len(files), '正在生成独立安装包……')
        job.finish(dict(url='/build/packages/' + name, path=str(path), count=len(entries)))
    except Exception as error:
        job.fail(error)
