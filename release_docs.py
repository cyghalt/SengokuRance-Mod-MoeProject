"""导出发布资源与角色、势力的精确绑定关系。"""
import csv
import io
import json
from pathlib import Path
from release_catalog import with_factions


def write_bindings(roster, base):
    base = Path(base)
    roster = with_factions(roster, base)
    faction = {cid: f['name'] for f in roster['factions'] for cid in f['characters']}
    rows = [dict(势力=faction[e['character_id']], 武将=e['character_name'], 角色ID=e['character_id'],
                 素材类型=e['kind'], 来源包=e['archive'], 原生文件=e['internal_name'], 指针=e['pointer_index'],
                 资源编号=e['resource_ids'], 别名指针=e['alias_pointer_indices'], 尺寸=[e['width'], e['height']],
                 替换文件=e['after_file'], 替换SHA256=e['after_sha256'], 预览原图=e['before_preview'],
                 预览替换=e['after_preview'], 来源批次=e['mod'], 差分=e['variant_id']) for e in roster['entries']]
    docs = base / 'docs'
    docs.mkdir(exist_ok=True)
    (docs / '角色资源绑定关系.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), 'utf8')
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=list(rows[0]) if rows else [], lineterminator='\n')
    writer.writeheader()
    writer.writerows({key: ','.join(map(str, value)) if isinstance(value, list) else value for key, value in row.items()} for row in rows)
    (docs / '角色资源绑定关系.csv').write_bytes(text.getvalue().encode('utf-8-sig'))
    (docs / 'README.md').write_text('# 发布版文档\n\n- [资源提取与替换说明](资源提取与替换说明.md)\n- [角色资源绑定关系 CSV](角色资源绑定关系.csv)\n- [完整绑定关系 JSON](角色资源绑定关系.json)\n\n'
        + f'本包登记 {len(roster["characters"])} 位武将、{len(rows)} 个物理来源位置。CSV 可直接用 Excel 打开。\n', 'utf8')
