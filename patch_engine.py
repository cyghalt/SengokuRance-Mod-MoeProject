"""发布版补丁引擎：读取游戏 ALD、按名单重编码并回填，不依赖开发仓库。

自包含实现，只需 Pillow 与同目录 tools/alice.exe。
- 用游戏内的原始成员字节作为模板，保留 QNT 版本/坐标与 AJP 遮罩、偏移。
- 重建 ALD 后逐个成员校验 SHA-256、头字段、别名与页脚。
- 安装前备份游戏包，失败自动回滚。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent
DATA = BASE / 'data'
ALICE = BASE / 'tools' / 'alice.exe'
KEY = bytes.fromhex('5d91ae874a5641cd83ec4c92b5cb1634')
GAME_FILES = ('rance7GA.ald', 'rance7GM.ald')
DEFAULT_GAME = Path('D:/Program Files (x86)/Steam/steamapps/common/Sengoku Rance')


class PatchError(Exception):
    """用户可见的安装/校验错误。"""


# ---------------------------------------------------------------- 基础工具

def u24(b):
    return int.from_bytes(b, 'little')


def read32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def cgmetrics(b):
    if b[:4] == b'QNT\0':
        version = read32(b, 4)
        shift = 4 if version else 0
        x, y, w, h, bpp = struct.unpack_from('<5I', b, 8 + shift)
        return dict(format='qnt', width=w, height=h, x=x, y=y,
                    alpha=bool(read32(b, 36 + shift)), version=version)
    if b[:4] == b'AJP\0':
        return dict(format='ajp', width=read32(b, 12), height=read32(b, 16),
                    x=0, y=0, alpha=bool(read32(b, 32)))
    raise PatchError('游戏成员不是 QNT/AJP 图像')


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def alice_convert(src, dst, alice=ALICE):
    result = subprocess.run([str(alice), 'cg', 'convert', str(src), str(dst)],
                            capture_output=True)
    if result.returncode:
        raise PatchError('alice 转换失败：' + result.stderr.decode('utf8', 'replace')[:300])


# ---------------------------------------------------------------- ALD 读写

def read_archive(path):
    """返回 (prefix, footer, entries)；entries 中别名成员形如 {'pointer','alias'}。"""
    size = path.stat().st_size
    with path.open('rb') as f:
        first = f.read(6)
        pointer_end = u24(first[:3]) * 256
        map_end = u24(first[3:]) * 256
        if not 0 < pointer_end <= map_end < size:
            raise PatchError('ALD 指针表结构异常：' + path.name)
        f.seek(0)
        prefix = f.read(map_end)
        addresses = [u24(prefix[i:i + 3]) * 256 for i in range(0, pointer_end - 2, 3)]
        try:
            count = addresses.index(size - 16, 1) - 1
        except ValueError:
            raise PatchError('ALD 成员数解析失败：' + path.name)
        if not (count > 0 and addresses[1] == map_end):
            raise PatchError('ALD 链接表结构异常：' + path.name)
        entries = []
        for pi in range(1, count + 1):
            start, end = addresses[pi:pi + 2]
            if not map_end <= start <= end <= size - 16:
                raise PatchError('ALD 成员地址越界：' + path.name)
            if start == end:
                entries.append(dict(pointer=pi, alias=pi + 1))
                continue
            f.seek(start)
            fixed = f.read(16)
            hs, length = struct.unpack_from('<II', fixed)
            if not (16 < hs <= 4096 and start + hs + length <= end):
                raise PatchError('ALD 成员头异常：' + path.name)
            header = fixed + f.read(hs - 16)
            data = f.read(length)
            entries.append(dict(pointer=pi, start=start, end=end, header=header,
                                length=length,
                                sha256=hashlib.sha256(data).hexdigest(),
                                name=header[16:].split(b'\0', 1)[0].decode('cp932')))
        f.seek(-16, 2)
        footer = f.read(16)
    return prefix, footer, entries


def encode_native(row, png, template, dst, alice=ALICE):
    """按模板字节把替换 PNG 重编码为原生 QNT/AJP，并逐像素往返校验。"""
    with Image.open(png) as opened:
        rgba = opened.convert('RGBA')
        if opened.size != (row['width'], row['height']):
            raise PatchError('替换图尺寸不符：' + str(png))
        alpha = rgba.getchannel('A')
        if not row['alpha'] and alpha.getextrema() != (255, 255):
            raise PatchError('原图无透明通道，替换图不能带透明：' + str(png))
        if row['format'] == 'ajp':
            if hashlib.sha256(alpha.tobytes()).hexdigest() != row['alpha_sha256']:
                raise PatchError('AJP 透明遮罩与原图不一致，需保留原遮罩：' + str(png))
            jpg = io.BytesIO()
            rgba.convert('RGB').save(jpg, 'JPEG', quality=95, subsampling=0)
            jpeg = bytearray(jpg.getvalue())
            for i in range(min(16, len(jpeg))):
                jpeg[i] ^= KEY[i]
            off = read32(template, 20)
            ms = read32(template, 32)
            mo = read32(template, 28)
            header = bytearray(template[:off])
            struct.pack_into('<I', header, 24, len(jpeg))
            struct.pack_into('<I', header, 28, off + len(jpeg) if ms else 0)
            dst.write_bytes(bytes(header) + bytes(jpeg) + template[mo:mo + ms])
        else:
            tmp = dst.with_suffix('.converted.qnt')
            alice_convert(png, tmp, alice)
            converted = tmp.read_bytes()
            tmp.unlink()
            nh = read32(converted, 8)
            ps = read32(converted, 36)
            az = read32(converted, 40)
            shift = 4 if read32(template, 4) else 0
            oh = read32(template, 8) if shift else 48
            header = bytearray(template[:oh])
            struct.pack_into('<I', header, 32 + shift, ps)
            struct.pack_into('<I', header, 36 + shift, az if row['alpha'] else 0)
            dst.write_bytes(bytes(header) + converted[nh:nh + ps]
                            + (converted[nh + ps:nh + ps + az] if row['alpha'] else b''))
        check = dst.with_suffix('.check.png')
        alice_convert(dst, check, alice)
        with Image.open(check) as decoded_img:
            decoded = decoded_img.convert('RGBA')
            if decoded.size != rgba.size:
                raise PatchError('回填像素尺寸不一致：' + str(png))
            if row['format'] == 'qnt' and decoded.tobytes() != rgba.tobytes():
                raise PatchError('QNT 像素往返不一致：' + str(png))
            if row['format'] == 'ajp' and decoded.getchannel('A').tobytes() != alpha.tobytes():
                raise PatchError('AJP 遮罩往返不一致：' + str(png))
        check.unlink()


def build_archive(game_archive, staged, planned, work_dir, alice=ALICE, log=None):
    """planned: {pointer_index: (row, after_png_path)}；写入并校验 staged。"""
    prefix, footer, entries = read_archive(game_archive)
    by_pointer = {e['pointer']: e for e in entries}
    link_start = u24(prefix[:3]) * 256
    volume = ord(game_archive.stem[-1].upper()) - 64
    encoded = {}
    with game_archive.open('rb') as original:
        for pi, (row, after_png) in planned.items():
            entry = by_pointer.get(pi)
            if entry is None or 'alias' in entry:
                raise PatchError('替换位置在游戏包中不存在或是别名：' + row['internal_name'])
            if entry['name'] != row['internal_name']:
                raise PatchError('游戏资源与名单不符：' + row['internal_name']
                                 + '（游戏内为 ' + entry['name'] + '）')
            pointers = {pi, *row.get('alias_pointer_indices', [])}
            ids = [i // 3 + 1 for i in range(0, len(prefix) - link_start - 2, 3)
                   if prefix[link_start + i] == volume
                   and int.from_bytes(prefix[link_start + i + 1:link_start + i + 3], 'little') in pointers]
            if sorted(ids) != sorted(row['resource_ids']):
                raise PatchError('游戏资源编号与名单不符：' + row['internal_name'])
            original.seek(entry['start'] + len(entry['header']))
            template = original.read(entry['length'])
            metrics = cgmetrics(template)
            for key in ('format', 'width', 'height', 'alpha'):
                if metrics[key] != row[key]:
                    raise PatchError('游戏资源格式与名单不符：' + row['internal_name'])
            if digest(after_png) != row['after_sha256']:
                raise PatchError('替换图已损坏或被改动：' + str(after_png))
            with Image.open(after_png) as png:
                if png.mode != 'RGBA' or png.size != (row['width'], row['height']):
                    raise PatchError('替换图须为 RGBA 且尺寸一致：' + str(after_png))
            native = work_dir / 'native' / game_archive.stem / f'{pi:05d}.qnt'
            native.parent.mkdir(parents=True, exist_ok=True)
            encode_native(row, after_png, template, native, alice)
            encoded[pi] = native
            if log:
                log(f'已编码 {row["internal_name"]}（{game_archive.name} #{pi}）')
    assert set(encoded) == set(planned)

    updated = bytearray(prefix)

    def set_pointer(pi, offset):
        if offset % 256 or offset >= 2 ** 32:
            raise PatchError('ALD 对齐/大小超限')
        updated[pi * 3:pi * 3 + 3] = (offset // 256).to_bytes(3, 'little')

    with game_archive.open('rb') as original, staged.open('wb') as target:
        target.write(prefix)
        for entry in entries:
            pi = entry['pointer']
            if 'alias' in entry:
                continue
            set_pointer(pi, target.tell())
            if pi in encoded:
                data = encoded[pi].read_bytes()
                header = bytearray(entry['header'])
                struct.pack_into('<I', header, 4, len(data))
                target.write(header)
                target.write(data)
                target.write(b'\0' * ((-target.tell()) % 256))
            else:
                original.seek(entry['start'])
                target.write(original.read(entry['end'] - entry['start']))
        for entry in reversed(entries):
            if 'alias' in entry:
                pi, alias = entry['pointer'], entry['alias']
                updated[pi * 3:pi * 3 + 3] = updated[alias * 3:alias * 3 + 3]
        set_pointer(len(entries) + 1, target.tell())
        target.write(footer)
        target.seek(0)
        target.write(updated)

    rebuilt_prefix, rebuilt_footer, rebuilt = read_archive(staged)
    if rebuilt_footer != footer or len(rebuilt) != len(entries):
        raise PatchError('重建 ALD 结构与原包不一致：' + game_archive.name)
    if rebuilt_prefix != updated:
        raise PatchError('重建 ALD 指针表不一致：' + game_archive.name)
    map_start = u24(prefix[:3]) * 256
    if rebuilt_prefix[map_start:] != prefix[map_start:]:
        raise PatchError('重建 ALD 链接表被改动：' + game_archive.name)
    for before, after in zip(entries, rebuilt):
        pi = before['pointer']
        if 'alias' in before:
            if after.get('alias') != before['alias']:
                raise PatchError('别名关系被破坏：' + game_archive.name)
            continue
        if after['header'][:4] != before['header'][:4] or after['header'][8:] != before['header'][8:]:
            raise PatchError('成员头字段被改动：' + game_archive.name)
        expected = digest(encoded[pi]) if pi in encoded else before['sha256']
        if after['sha256'] != expected:
            raise PatchError('成员负载校验失败：' + game_archive.name + ' #' + str(pi))
    return len(entries)


# ---------------------------------------------------------------- 名单与安装

def release_dir():
    return BASE


def load_roster(base=BASE):
    path = Path(base) / 'data' / 'roster.json'
    if not path.is_file():
        raise PatchError('缺少名单文件 data/roster.json，请先运行 build_release.py 生成发布版。')
    return json.loads(path.read_text('utf8'))


def entry_by_id(roster):
    return {e['id']: e for e in roster['entries']}


def detect_game(path):
    """校验所选目录是否是战国兰斯游戏目录，返回缺失项列表。"""
    path = Path(path)
    found = [name for name in GAME_FILES if (path / name).is_file()]
    other = sorted(p.name for p in path.glob('*.ald')) if path.is_dir() else []
    return dict(path=str(path), found=found, other_ald=other, ok=len(found) == len(GAME_FILES))


def read_members(path):
    """只解析成员头，不读取负载：{pointer: {start,header_len,length,name} 或 {alias}}。"""
    size = path.stat().st_size
    with path.open('rb') as f:
        first = f.read(6)
        pointer_end = u24(first[:3]) * 256
        map_end = u24(first[3:]) * 256
        if not 0 < pointer_end <= map_end < size:
            raise PatchError('ALD 指针表结构异常：' + path.name)
        f.seek(0)
        prefix = f.read(pointer_end)
        addresses = [u24(prefix[i:i + 3]) * 256 for i in range(0, pointer_end - 2, 3)]
        try:
            count = addresses.index(size - 16, 1) - 1
        except ValueError:
            raise PatchError('ALD 成员数解析失败：' + path.name)
        members = {}
        for pi in range(1, count + 1):
            start, end = addresses[pi:pi + 2]
            if start == end:
                members[pi] = dict(alias=pi + 1)
                continue
            f.seek(start)
            fixed = f.read(16)
            hs, length = struct.unpack_from('<II', fixed)
            if not (16 < hs <= 4096 and start + hs + length <= end):
                raise PatchError('ALD 成员头异常：' + path.name)
            header = fixed + f.read(hs - 16)
            members[pi] = dict(start=start, header_len=hs, length=length,
                               name=header[16:].split(b'\0', 1)[0].decode('cp932'))
    return members


def inspect_game(roster, game_dir, progress=None, log=None):
    """读取游戏包中目标成员，判断是否仍为原图。返回 {id: '未替换'|'已替换'|...}。"""
    game_dir = Path(game_dir)
    by_archive = {}
    for e in roster['entries']:
        by_archive.setdefault(e['archive'], []).append(e)
    result = {}
    for archive, ents in by_archive.items():
        path = game_dir / archive
        if not path.is_file():
            for e in ents:
                result[e['id']] = '缺文件'
            continue
        members = read_members(path)
        with path.open('rb') as f:
            for n, e in enumerate(ents, 1):
                member = members.get(e['pointer_index'])
                state = '未知'
                if member and 'alias' not in member:
                    f.seek(member['start'] + member['header_len'])
                    data = f.read(member['length'])
                    h = hashlib.sha256(data).hexdigest()
                    if e.get('pristine_sha256') and h == e['pristine_sha256']:
                        state = '未替换'
                    else:
                        state = '已替换'
                result[e['id']] = state
                if progress and (n % 25 == 0 or n == len(ents)):
                    progress(n / len(ents), f'{archive} 检查 {n}/{len(ents)}')
        if log:
            log(f'{archive} 状态检查完成（{len(ents)} 项）')
    return result



def plan_selection(roster, selected_ids):
    """把选中的名单项按归档分组：{archive: {pointer: (row, after_png)}}。"""
    chosen = entry_by_id(roster)
    unknown = [i for i in selected_ids if i not in chosen]
    if unknown:
        raise PatchError('名单中不存在这些资源：' + ', '.join(unknown[:5]))
    planned = {}
    seen = {}
    for asset in sorted(chosen):
        if asset not in selected_ids:
            continue
        row = chosen[asset]
        archive = row['archive']
        slot = (archive, row['pointer_index'])
        if slot in seen and seen[slot] != asset:
            raise PatchError('重叠的替换记录：' + asset)
        seen[slot] = asset
        planned.setdefault(archive, {})[row['pointer_index']] = (
            row, BASE / row['after_file'])
    return planned


def install(roster, game_dir, selected_ids, do_install=True, progress=None, log=None):
    game_dir = Path(game_dir).resolve()
    info = detect_game(game_dir)
    if not info['ok']:
        raise PatchError('所选目录不是战国兰斯游戏目录（缺少 ' + '、'.join(GAME_FILES) + '）。')
    if not ALICE.is_file():
        raise PatchError('缺少 tools/alice.exe，请按 tools/README.md 从上游获取 alice-tools。')
    planned = plan_selection(roster, selected_ids)
    if not planned:
        raise PatchError('没有选中任何资源。')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    work = BASE / 'build' / ('安装-' + stamp)
    work.mkdir(parents=True, exist_ok=True)

    def report(stage, frac, message):
        if progress:
            progress(frac, message)
        if log:
            log(message)

    items = []
    total = sum(len(v) for v in planned.values())
    done = 0
    def on_encoded(message):
        nonlocal done
        done += 1
        report('encode', 0.05 + 0.7 * done / total, message)

    # 1. 编码并重建每个归档（先在发布版内构建并校验，未安装时不写入游戏目录）
    for archive, entries in planned.items():
        source = game_dir / archive
        report('encode', 0.05, f'准备 {archive}（{len(entries)} 项）')
        before = digest(source)
        built = work / archive
        members = build_archive(source, built, entries, work, log=on_encoded)
        if digest(source) != before:
            raise PatchError('构建期间游戏包被改动：' + archive)
        items.append(dict(archive=archive, target=str(source), before_sha256=before,
                          built=str(built), installed_sha256=digest(built),
                          replacements=len(entries), members_verified=members))
        report('encode', 0.75 + 0.05 * len(items), f'{archive} 校验通过（{members} 个成员）')

    result = dict(passed=True, installed=False, game=str(game_dir), generated_at=stamp,
                  source_positions=total,
                  items=[{k: v for k, v in it.items()} for it in items],
                  checks=['替换图哈希与尺寸', 'QNT/AJP 逐像素往返', '全部 ALD 成员负载与头字段',
                          'ALD 指针、别名与页脚'],
                  game_launch_verified=False, work=str(work))

    if not do_install:
        save_json(work / '安装报告.json', result)
        report('done', 1.0, '仅构建校验完成（未安装）；重建的游戏包在 ' + str(work))
        return result

    # 2. 备份并原子替换
    backup = work / 'game-backups'
    backup.mkdir(parents=True, exist_ok=True)
    staged, replaced = [], []
    try:
        for it in items:
            target = Path(it['target'])
            if digest(target) != it['before_sha256']:
                raise PatchError('安装前游戏包发生变化：' + it['archive'])
            saved = backup / it['archive']
            shutil.copy2(target, saved)
            if digest(saved) != it['before_sha256']:
                raise PatchError('备份校验失败：' + it['archive'])
            it['backup'] = str(saved)
            stage = target.with_name(target.name + '.moe-stage')
            if stage.exists():
                raise PatchError('存在未完成的暂存文件，请先处理：' + stage.name)
            shutil.copy2(it['built'], stage)
            if digest(stage) != it['installed_sha256']:
                raise PatchError('暂存文件校验失败：' + it['archive'])
            staged.append((it, stage))
        report('install', 0.9, '备份完成，正在替换游戏包……')
        for it in items:
            target = Path(it['target'])
            if digest(target) != it['before_sha256']:
                raise PatchError('替换前游戏包发生变化：' + it['archive'])
        for it, stage in staged:
            os.replace(stage, it['target'])
            replaced.append(it)
        for it in items:
            if digest(it['target']) != it['installed_sha256']:
                raise PatchError('安装后哈希校验失败：' + it['archive'])
    except BaseException:
        for it in replaced:
            target = Path(it['target'])
            restore = target.with_name(target.name + '.moe-restore')
            shutil.copy2(it['backup'], restore)
            os.replace(restore, target)
        for _it, stage in staged:
            Path(stage).unlink(missing_ok=True)
        raise
    finally:
        for _it, stage in staged:
            Path(stage).unlink(missing_ok=True)
        for it in items:
            Path(it['built']).unlink(missing_ok=True)

    result.update(installed=True, installed_at=datetime.datetime.now().astimezone().isoformat(),
                  backup_directory=str(backup))
    save_json(work / '安装报告.json', result)
    save_json(BASE / 'build' / 'last-backup.json',
              dict(at=stamp, game=str(game_dir), backup=str(backup),
                   archives=[it['archive'] for it in items]))
    report('done', 1.0, f'安装完成：{total} 个来源位置已替换。')
    return result


def restore(game_dir, backup_dir, progress=None, log=None):
    backup_dir = Path(backup_dir)
    game_dir = Path(game_dir).resolve()
    archives = [p.name for p in sorted(backup_dir.glob('*.ald'))]
    if not archives:
        raise PatchError('备份目录中没有游戏包：' + str(backup_dir))
    for name in archives:
        saved = backup_dir / name
        target = game_dir / name
        stage = target.with_name(target.name + '.moe-restore-stage')
        shutil.copy2(saved, stage)
        os.replace(stage, target)
        if progress:
            progress(1.0, f'已恢复 {name}')
    if log:
        log('已从备份恢复：' + '、'.join(archives))
    return dict(restored=archives, game=str(game_dir), backup=str(backup_dir))


# ---------------------------------------------------------------- 命令行

def main(argv=None):
    parser = argparse.ArgumentParser(description='发布版补丁引擎（命令行）')
    parser.add_argument('--game', type=Path, default=DEFAULT_GAME)
    parser.add_argument('--list', action='store_true', help='列出可替换名单')
    parser.add_argument('--all', action='store_true', help='替换名单中的全部资源')
    parser.add_argument('--id', action='append', default=[], help='按名单 id 指定资源，可重复')
    parser.add_argument('--mod', action='append', default=[], help='按批次(mod)选择资源，可重复')
    parser.add_argument('--install', action='store_true', help='校验通过后写入游戏目录')
    parser.add_argument('--restore', type=Path, metavar='备份目录', help='从备份目录恢复游戏包')
    args = parser.parse_args(argv)

    if args.list:
        roster = load_roster()
        for group in roster['groups']:
            print(f"[{group['title']}]  ({group['mod']}, {group['count']} 项)")
            for idx in group['entries']:
                e = roster['entries'][idx]
                print(f"    {e['id']}  {e['internal_name']}  {e['width']}x{e['height']}"
                      f"  {e['archive']}")
        print(f"共计 {roster['total_entries']} 个来源位置 / {len(roster['groups'])} 个批次")
        return 0

    if args.restore:
        print(json.dumps(restore(args.game, args.restore, progress=lambda f, m: print(m)),
                         ensure_ascii=False))
        return 0

    roster = load_roster()
    if args.all:
        selected = {e['id'] for e in roster['entries']}
    else:
        selected = set(args.id)
        if args.mod:
            for group in roster['groups']:
                if group['mod'] in args.mod:
                    selected |= {roster['entries'][i]['id'] for i in group['entries']}
    if not selected:
        parser.error('请用 --all、--id 或 --mod 指定要替换的资源')
    report = install(roster, args.game, selected, do_install=args.install,
                     progress=lambda f, m: print(f'{f*100:5.1f}%  {m}', flush=True))
    print(json.dumps({k: report[k] for k in ('passed', 'installed', 'source_positions')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except PatchError as error:
        print('错误：' + str(error), file=sys.stderr)
        sys.exit(1)
