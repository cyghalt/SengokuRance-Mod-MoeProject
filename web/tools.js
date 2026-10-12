'use strict';
document.querySelector('.tabs').addEventListener('click', e => {
  if (!e.target.dataset.tab) return;
  const tab = e.target.dataset.tab;
  ['patch', 'resources', 'script'].forEach(t => $(t + 'Panel').classList.toggle('hidden', t !== tab));
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  if (tab === 'resources') loadResources();
});
$('btnExtract').onclick = async () => {
  if (S.busy || !(await detect())) return;
  try {
    const job = await post('/api/extract', {game: $('game').value.trim()});
    const result = await pollJob(job.job);
    if (result) { S.localOriginals = true; renderList(); $('extractInfo').textContent = result.message + ' ' + result.workspace; await loadResources(); }
  } catch (e) { alert(e.message); } finally { setBusy(false); }
};
$('btnPackage').onclick = async () => {
  if (S.busy) return;
  if (!S.selected.size) { alert('请先勾选角色或势力。'); return; }
  try {
    const job = await post('/api/package', {ids: [...S.selected]});
    const result = await pollJob(job.job);
    if (result) {
      log('独立安装包已保存：' + result.path);
      const link = document.createElement('a'); link.href = result.url; link.download = result.path.split(/[\\/]/).pop(); link.click();
    }
  } catch (e) { alert(e.message); } finally { setBusy(false); }
};
let resourceOffset = 0, resourceTotal = 0, scriptOffset = 0, scriptTotal = 0;
let resourceSequence = 0, scriptSequence = 0;
async function loadResources() {
  const sequence = ++resourceSequence;
  try {
    const data = await api('/api/resources?q=' + encodeURIComponent($('resourceQuery').value.trim()) + '&offset=' + resourceOffset);
    if (sequence !== resourceSequence) return;
    resourceTotal = data.total;
    $('extractInfo').textContent = data.source ? '当前资源工作区：' + data.source : '请先解压资源。';
    $('resourceResults').innerHTML = data.results.map(r => `<a class="resource-item" href="${esc(r.url)}" target="_blank"><img loading="lazy" src="${esc(r.url)}" alt="${esc(r.internal_name)}"><strong>${esc(r.internal_name)}</strong><span>${esc(r.archive)} · ${r.width}×${r.height}</span><span>编号 ${esc(r.resource_ids.join(', '))}</span></a>`).join('');
    $('resourceCount').textContent = `${resourceTotal} 张 · 第 ${Math.floor(resourceOffset / 24) + 1} 页`;
    $('resourcePrev').disabled = resourceOffset === 0; $('resourceNext').disabled = resourceOffset + 24 >= resourceTotal;
  } catch (e) { $('extractInfo').textContent = e.message; }
}
$('resourceSearch').onclick = () => { resourceOffset = 0; loadResources(); };
$('resourceQuery').onkeydown = e => { if (e.key === 'Enter') $('resourceSearch').click(); };
$('resourcePrev').onclick = () => { resourceOffset = Math.max(0, resourceOffset - 24); loadResources(); };
$('resourceNext').onclick = () => { if (resourceOffset + 24 < resourceTotal) { resourceOffset += 24; loadResources(); } };
function highlight(value, query) {
  if (!query) return esc(value);
  const pattern = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return String(value).split(new RegExp('(' + pattern + ')', 'gi')).map((part, i) => i % 2 ? '<mark>' + esc(part) + '</mark>' : esc(part)).join('');
}
async function searchScript() {
  const sequence = ++scriptSequence;
  const q = $('scriptQuery').value.trim();
  if (!q) return;
  $('scriptSource').textContent = '正在搜索……';
  try {
    const data = await api('/api/search?q=' + encodeURIComponent(q) + '&kind=' + $('scriptKind').value + '&offset=' + scriptOffset);
    if (sequence !== scriptSequence) return;
    scriptTotal = data.total;
    $('scriptSource').textContent = '搜索文本来源：' + data.source;
    $('scriptResults').innerHTML = data.results.map(r => `<article class="script-result"><div><strong>${r.kind === 'messages' ? '消息' : '字符串'} #${r.id}</strong> <a href="${esc(r.url)}" target="_blank">打开编辑 JSON</a></div><p>${highlight(r.text, q)}</p><div class="muted">函数：${esc(r.functions.join(' / ') || '无引用')}</div><details><summary>相邻文本与文件位置</summary>${r.context.map(c => `<p class="${c.match ? 'match' : ''}">#${c.id} ${highlight(c.text, q)}</p>`).join('')}<code>${esc(r.file)}</code></details></article>`).join('') || '<p>没有匹配结果。</p>';
    $('scriptCount').textContent = `${scriptTotal} 条 · 第 ${Math.floor(scriptOffset / 20) + 1} 页`;
    $('scriptPrev').disabled = scriptOffset === 0; $('scriptNext').disabled = scriptOffset + 20 >= scriptTotal;
  } catch (e) { $('scriptSource').textContent = e.message; }
}
$('scriptForm').onsubmit = e => { e.preventDefault(); scriptOffset = 0; searchScript(); };
$('scriptPrev').onclick = () => { scriptOffset = Math.max(0, scriptOffset - 20); searchScript(); };
$('scriptNext').onclick = () => { if (scriptOffset + 20 < scriptTotal) { scriptOffset += 20; searchScript(); } };
