"""角色名单与可配置势力；只使用替换记录和已确认的角色关系。"""
import json
from pathlib import Path


def make_characters(entries, names, owners):
    characters = {}
    for index, entry in enumerate(entries):
        cid = entry.get('character_id')
        confirmed = owners.get(entry['id'], [])
        if not cid and len(confirmed) == 1:
            cid = confirmed[0]
        cid = cid or 'unbound:' + entry['mod']
        entry['character_id'] = cid
        entry['character_name'] = names.get(cid, entry.get('character_name') or entry['mod'])
        size = (entry['width'], entry['height'])
        kind = 'portrait' if size in ((186, 220), (190, 220), (65, 72)) else (
            'battle' if size in ((270, 270), (340, 290)) else 'other')
        entry['kind'] = kind
        role = characters.setdefault(cid, dict(id=cid, name=entry['character_name'], entries=[],
                                               portrait=None, battle=None, previews={'portrait': [], 'battle': []}))
        role['entries'].append(index)
        if kind != 'other':
            previews = role['previews'][kind]
            # 跨包同图只显示一次，但全部物理位置仍加入安装集合。
            if not any(entries[i]['after_sha256'] == entry['after_sha256'] and
                       entries[i]['before_sha256'] == entry['before_sha256'] for i in previews):
                previews.append(index)
    for role in characters.values():
        for kind in ('portrait', 'battle'):
            role['previews'][kind].sort(key=lambda i: (entries[i]['width'] == 65,
                                                      entries[i]['width'] == 340, entries[i]['id']))
            role[kind] = next(iter(role['previews'][kind]), None)
    return sorted(characters.values(), key=lambda role: role['name'])


def with_factions(roster, base):
    value = json.loads((Path(base) / 'factions.json').read_text('utf8'))
    scope_path = Path(base) / 'release_scope.json'
    scope = json.loads(scope_path.read_text('utf8')) if scope_path.exists() else {}
    excluded = set(scope.get('excluded_characters', []))
    display = value.get('display_names', {}) | scope.get('display_names', {})
    roster = dict(roster, characters=[dict(c, name=display.get(c['id'], c['name'])) for c in roster['characters']],
                  entries=[dict(e, character_name=display.get(e['character_id'], e['character_name'])) for e in roster['entries']])
    known = {c['id']: c for c in roster['characters']}
    names = {c['name']: c['id'] for c in roster['characters']}
    assigned, groups, keys = set(), [], set()
    for faction in value['factions']:
        key = faction['id']
        if key in keys or key == 'unassigned':
            raise ValueError('重复或保留的势力 ID：' + key)
        keys.add(key)
        ids = []
        for member in faction['characters']:
            if member in excluded:
                continue
            cid = member if member in known else names.get(member)
            if cid is None:
                raise ValueError('势力名单含未知角色：' + member)
            if cid in assigned:
                raise ValueError('角色被重复分配：' + member)
            assigned.add(cid)
            ids.append(cid)
        groups.append(dict(id=key, name=faction['name'], characters=ids))
    groups.append(dict(id='unassigned', name='待分配势力', characters=[cid for cid in known if cid not in assigned]))
    return dict(roster, factions=groups)
