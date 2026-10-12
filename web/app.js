'use strict';

const S = {
  entries: [], groups: [], byId: {}, groupByMod: {},
  selected: new Set(), status: {}, order: [], current: null,
  busy: false, total: 0,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, options) {
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); } catch (e) { throw new Error('服务返回异常'); }
  if (data && data.error) throw new Error(data.error);
  if (!response.ok) throw new Error('请求失败（' + response.status + '）');
  return data;
}

function post(path, body) {
  return api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}

// 角色卡片按势力组织，每个角色勾选全部来源位置。
function roleEntries(role) { return role.entries.map(i => S.entries[i]); }
function pair(index, title) {
  if (index == null) return `<div class="empty-pair">${esc(title)}：暂无替换</div>`;
  const e = S.entries[index];
  const image = (path, label) => path ? `<figure><img loading="lazy" src="/${esc(path)}" alt="${label}"><figcaption>${label}</figcaption></figure>` : `<figure>提取自己的游戏资源后显示对照</figure>`;
  const before = S.localOriginals ? 'api/local-preview?id=' + encodeURIComponent(e.id) : null;
  return `<div class="pair-label">${title}</div><div class="card-pair">${image(before, '本机资源')}${image(e.after_preview, '替换后')}</div>`;
}
function renderList() {
  const q = $('filter').value.trim().toLowerCase();
  let shown = 0;
  S.order = [];
  $('list').innerHTML = S.factions.map(f => {
    const roles = f.characters.map(id => S.roleById[id]).filter(r =>
      [r.name, r.id, f.name, ...roleEntries(r).flatMap(e => [e.internal_name, e.mod, ...(e.resource_ids || [])])].join(' ').toLowerCase().includes(q));
    if (!roles.length) return '';
    shown += roles.length;
    S.order.push(...roles.map(r => r.id));
    const all = roles.flatMap(roleEntries);
    const count = all.filter(e => S.selected.has(e.id)).length;
    return `<details class="group" data-faction="${esc(f.id)}"><summary><input type="checkbox" class="gcheck" ${count === all.length ? 'checked' : ''}><span class="gtitle">${esc(f.name)}</span><span class="gcount">${roles.length} 位角色 · ${all.length} 个位置</span></summary><div class="role-grid">${roles.map(r => {
      const entries = roleEntries(r), picked = entries.filter(e => S.selected.has(e.id)).length;
      const states = [...new Set(entries.map(e => S.status[e.id]).filter(Boolean))];
      return `<article class="role-card" data-role="${esc(r.id)}"><div class="role-head"><input type="checkbox" class="rcheck" ${picked === entries.length ? 'checked' : ''}><button class="role-name" data-detail="${esc(r.id)}">${esc(r.name)}</button><span class="muted">${entries.length} 项 · ${esc(states.join(' / ') || '未检测')}</span></div>${pair(r.portrait, '头像')}${r.battle == null ? '' : pair(r.battle, '战斗形象')}<button class="ghost card-detail" data-detail="${esc(r.id)}">查看表情、动作及全部资源</button></article>`;
    }).join('')}</div></details>`;
  }).join('');
  $('counts').textContent = `${S.characters.length} 位角色 / ${S.total} 个来源位置；当前显示 ${shown} 位，已勾选 ${S.selected.size} 项`;
  $('btnInstallSel').textContent = `替换选中角色（${S.selected.size} 项）`;
  $('btnInstallAll').textContent = `一键替换全部 ${S.total} 项`;
  refreshMarks();
}
function refreshMarks() {
  document.querySelectorAll('.role-card').forEach(card => {
    const entries = roleEntries(S.roleById[card.dataset.role]);
    const n = entries.filter(e => S.selected.has(e.id)).length;
    card.querySelector('.rcheck').checked = n === entries.length;
    card.querySelector('.rcheck').indeterminate = n > 0 && n < entries.length;
    card.classList.toggle('picked', n > 0);
  });
  document.querySelectorAll('[data-faction]').forEach(group => {
    const entries = [...group.querySelectorAll('.role-card')].flatMap(c => roleEntries(S.roleById[c.dataset.role]));
    const n = entries.filter(e => S.selected.has(e.id)).length;
    group.querySelector('.gcheck').checked = n === entries.length;
    group.querySelector('.gcheck').indeterminate = n > 0 && n < entries.length;
  });
  $('counts').textContent = $('counts').textContent.replace(/已勾选 \d+ 项/, `已勾选 ${S.selected.size} 项`);
  $('btnInstallSel').textContent = `替换选中角色（${S.selected.size} 项）`;
}
function toggleRole(id, on) { roleEntries(S.roleById[id]).forEach(e => on ? S.selected.add(e.id) : S.selected.delete(e.id)); }
$('list').addEventListener('click', event => {
  const card = event.target.closest('.role-card');
  if (event.target.classList.contains('rcheck') && card) { toggleRole(card.dataset.role, event.target.checked); refreshMarks(); }
  if (event.target.classList.contains('gcheck')) {
    event.stopPropagation();
    event.target.closest('.group').querySelectorAll('.role-card').forEach(c => toggleRole(c.dataset.role, event.target.checked));
    refreshMarks();
  }
  const button = event.target.closest('[data-detail]');
  if (button) showDetail(button.dataset.detail);
});
$('filter').addEventListener('input', renderList);
document.querySelector('.toolbar').addEventListener('click', event => {
  const act = event.target.dataset.act;
  if (act === 'all') S.entries.forEach(e => S.selected.add(e.id));
  if (act === 'none') S.selected.clear();
  if (act === 'invert') S.characters.forEach(r => toggleRole(r.id, !roleEntries(r).every(e => S.selected.has(e.id))));
  if (act === 'expand' || act === 'collapse') document.querySelectorAll('.group').forEach(d => d.open = act === 'expand');
  if (act === 'csv') exportCsv();
  refreshMarks();
});
function showDetail(id) {
  const role = S.roleById[id];
  if (!role) return;
  S.current = id;
  $('detailTitle').textContent = role.name;
  $('rolePreview').innerHTML = ['portrait', 'battle'].map(kind => {
    const indices = role.previews[kind];
    if (!indices.length) return '';
    return `<div>${pair(indices[0], kind === 'portrait' ? '头像' : '战斗形象')}<select data-preview="${kind}">${indices.map(i => `<option value="${i}">${esc(S.entries[i].variant_id || S.entries[i].internal_name)} · ${S.entries[i].width}×${S.entries[i].height}</option>`).join('')}</select></div>`;
  }).join('');
  $('meta').innerHTML = `<dt>角色 ID</dt><dd>${esc(role.id)}</dd><dt>完整资源</dt><dd>${roleEntries(role).map(e => `${esc(e.internal_name)} · ${e.width}×${e.height} · ${esc(e.archive)} · ${esc(S.status[e.id] || '未检测')}`).join('<br>')}</dd>`;
  $('detailDialog').showModal();
}
$('rolePreview').addEventListener('change', event => {
  if (!event.target.dataset.preview) return;
  const select = event.target;
  const title = select.dataset.preview === 'portrait' ? '头像' : '战斗形象';
  const container = select.parentElement;
  container.querySelectorAll('.pair-label,.card-pair,.empty-pair').forEach(el => el.remove());
  container.insertAdjacentHTML('afterbegin', pair(Number(select.value), title));
});
$('detailClose').onclick = () => $('detailDialog').close();
function step(delta) {
  if (!S.order.length) return;
  const index = Math.max(0, S.order.indexOf(S.current));
  $('detailDialog').close();
  showDetail(S.order[(index + delta + S.order.length) % S.order.length]);
}
$('btnPrev').onclick = () => step(-1);
$('btnNext').onclick = () => step(1);

// ------------------------------------------------------------------ 游戏目录

function setHint(text, cls) {
  const node = $('gameHint');
  node.textContent = text;
  node.className = 'hint ' + (cls || '');
}

async function detect(quiet) {
  const path = $('game').value.trim();
  if (!path) { setHint(''); return false; }
  try {
    const info = await api('/api/detect?path=' + encodeURIComponent(path));
    if (info.ok) { setHint('✓ 已找到游戏包', 'ok'); post('/api/settings', { game: path }); return true; }
    const missing = (info.found ? '' : '');
    setHint('✗ 缺少 ' + (path ? 'rance7GA.ald / rance7GM.ald' : ''), 'bad');
    return false;
  } catch (error) {
    if (!quiet) setHint('✗ ' + error.message, 'bad');
    return false;
  }
}

$('game').addEventListener('change', () => detect(true));
$('btnBrowse').addEventListener('click', async () => {
  setBusy(true, '请在弹窗中选择游戏目录……');
  try {
    const result = await api('/api/browse');
    if (result.path) { $('game').value = result.path; await detect(); }
  } catch (error) { alert(error.message); } finally { setBusy(false); }
});

$('btnDetect').addEventListener('click', async () => {
  if (!(await detect())) { alert('请先选择正确的游戏目录。'); return; }
  setBusy(true, '正在读取游戏包……');
  try {
    const result = await api('/api/state?path=' + encodeURIComponent($('game').value.trim()));
    S.status = result.states || {};
    renderList();
    const values = Object.values(S.status);
    log(`状态检测完成：未替换 ${values.filter((v) => v === '未替换').length} 项，` +
        `已替换 ${values.filter((v) => v === '已替换').length} 项。`);
  } catch (error) { alert(error.message); } finally { setBusy(false); }
});

// ------------------------------------------------------------------ 安装 / 恢复

function setBusy(on, text) {
  S.busy = on;
  $('busy').classList.toggle('hidden', !on);
  if (text) $('busyText').textContent = text;
  ['btnInstallSel', 'btnInstallAll', 'btnRestore', 'btnDetect', 'btnBrowse', 'btnExtract', 'btnPackage']
    .forEach((id) => { $(id).disabled = on; });
  $('btnRestore').disabled = on || !S.backup;
}

function setProgress(percent, message) {
  $('bar').value = percent;
  $('status').textContent = message || '就绪';
  if (S.busy && message) $('busyText').textContent = message;
}

function log(line) {
  const node = $('log');
  node.textContent += (node.textContent ? '\n' : '') + line;
  node.scrollTop = node.scrollHeight;
}

async function pollJob(id) {
  setBusy(true, '正在处理任务，请保持服务运行……');
  for (;;) {
    const snapshot = await api('/api/job?id=' + encodeURIComponent(id));
    setProgress((snapshot.frac || 0) * 100, snapshot.message);
    if (snapshot.log && snapshot.log.length) $('log').textContent = snapshot.log.join('\n');
    const node = $('log'); node.scrollTop = node.scrollHeight;
    if (snapshot.state === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 600));
      continue;
    }
    setBusy(false);
    if (snapshot.state === 'error') {
      alert('操作失败：' + snapshot.error);
      return null;
    }
    setProgress(100, '完成');
    return snapshot.result;
  }
}

async function install(ids, label) {
  if (S.busy) return;
  if (!(await detect())) { alert('请先选择正确的游戏目录。'); return; }
  if (!ids.length) { alert('请先勾选要替换的素材，或使用「一键替换全部」。'); return; }
  const path = $('game').value.trim();
  if (!confirm(`将替换 ${ids.length} 个来源位置，并在写盘前备份游戏包。\n\n` +
               `游戏目录：${path}\n\n请确认游戏已经完全退出。是否继续？`)) return;
  log(`开始${label}：${ids.length} 个来源位置。`);
  try {
    const job = await post('/api/install', { game: path, ids });
    const result = await pollJob(job.job);
    if (result) {
      log(`完成：${result.source_positions} 个来源位置已替换。`);
      log('备份目录：' + (result.backup_directory || ''));
      alert(`已替换 ${result.source_positions} 个来源位置。\n\n备份目录：\n` +
            `${result.backup_directory}\n\n报告：${result.work}\\安装报告.json\n\n` +
            '请启动游戏确认效果；如需还原可点击「从备份恢复」。');
      await loadBackup();
    }
  } catch (error) { alert('失败：' + error.message); log('失败：' + error.message); }
  finally { setBusy(false); }
}

$('btnInstallSel').addEventListener('click', () => install([...S.selected], '替换选中'));
$('btnInstallAll').addEventListener('click', () => install(S.entries.map((e) => e.id), '替换全部'));

async function loadBackup() {
  try {
    const data = await api('/api/roster');  // 复用同一接口读取备份信息
    const backup = data.backup;
    S.backup = !!backup;
    $('btnRestore').disabled = !backup;
    $('btnRestore').title = backup ? `备份于 ${backup.at}` : '暂无备份';
  } catch (error) { /* 忽略 */ }
}

$('btnRestore').addEventListener('click', async () => {
  if (S.busy) return;
  const data = await api('/api/roster');
  const backup = data.backup;
  if (!backup) { alert('没有可用的备份记录。'); return; }
  const game = $('game').value.trim() || backup.game;
  if (!confirm(`将用备份覆盖游戏包（${(backup.archives || []).join('、')}）。\n\n` +
               `备份：${backup.backup}\n游戏：${game}\n\n是否继续？`)) return;
  try {
    const job = await post('/api/restore', { game });
    const result = await pollJob(job.job);
    if (result) { log('已从备份恢复游戏包。'); alert('游戏包已恢复为备份时的状态。'); S.status = {}; renderList(); }
  } catch (error) { alert('失败：' + error.message); }
  finally { setBusy(false); }
});

$('btnQuit').addEventListener('click', async () => {
  if (!confirm('关闭本机服务？页面将无法再操作（可重新双击启动脚本）。')) return;
  try { await api('/api/quit'); } catch (error) { /* 服务已退出 */ }
  document.body.innerHTML = '<p style="padding:24px">服务已关闭，可关闭此页面。' +
    '</p>';
});

// ------------------------------------------------------------------ CSV

function exportCsv() {
  const head = ['势力 / 角色', '素材文件名', '资源编号', '尺寸', '归档', '游戏状态', '替换后文件'];
  const lines = [head.join(',')];
  const cell = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
  for (const role of S.characters) {
    for (const entry of roleEntries(role)) {
      const faction = S.factions.find(f => f.characters.includes(role.id));
      lines.push([`${faction.name} / ${role.name}`, entry.internal_name, (entry.resource_ids || []).join(','),
        entry.width + 'x' + entry.height, entry.archive, S.status[entry.id] || '未检测',
        entry.after_file].map(cell).join(','));
    }
  }
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = '可替换名单.csv';
  link.click();
  URL.revokeObjectURL(url);
  log('名单已导出为 CSV。');
}

// ------------------------------------------------------------------ 启动

(async function start() {
  try {
    const data = await api('/api/roster');
    S.localOriginals = data.originals_available;
    S.entries = data.roster.entries;
    if (data.package_index) {
      $('packageIndex').href = data.package_index;
      $('packageIndex').classList.remove('hidden');
    }
    S.groups = data.roster.groups;
    S.characters = data.roster.characters;
    S.factions = data.roster.factions;
    S.roleById = Object.fromEntries(S.characters.map(r => [r.id, r]));
    S.total = data.roster.total_entries;
    S.byId = {};
    S.entries.forEach((entry) => { S.byId[entry.id] = entry; });
    S.groupByMod = {};
    S.groups.forEach((group) => { S.groupByMod[group.mod] = group; });
    $('serviceInfo').textContent =
      `本地服务 ${location.host} · ${S.total} 个来源位置 / ${S.characters.length} 位角色`;
    $('game').value = (data.settings && data.settings.game) || data.roster.game_default || '';
    renderList();
    if ($('game').value) { if (await detect(true)) $('btnDetect').click(); }
    loadBackup();
    log('界面就绪。选择游戏目录后可「检测状态」「一键替换全部」或勾选后「替换选中」。');
  } catch (error) {
    alert('初始化失败：' + error.message);
  }
})();
