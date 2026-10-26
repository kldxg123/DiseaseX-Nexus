(() => {
  "use strict";
  const tier = document.getElementById("p1-10-data-tier");
  const panel = document.getElementById("p1-10-preview-panel");
  if (!tier || !panel) return;
  const trusted = [document.querySelector(".daily-update"), document.querySelector(".filters"), document.getElementById("empty"), document.getElementById("summary"), document.querySelector(".charts")].filter(Boolean);
  const source = document.getElementById("p1-10-preview-source");
  const state = document.getElementById("p1-10-preview-state");
  const municipality = document.getElementById("p1-10-preview-municipality");
  const start = document.getElementById("p1-10-preview-start");
  const end = document.getElementById("p1-10-preview-end");
  const status = document.getElementById("p1-10-preview-status");
  const summary = document.getElementById("p1-10-preview-summary");
  const table = document.getElementById("p1-10-preview-table");
  const trend = document.getElementById("p1-10-preview-trend");
  const geo = document.getElementById("p1-10-preview-geo");
  const quality = document.getElementById("p1-10-preview-quality");
  let publications = [];
  const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  async function options() {
    const response = await fetch("/api/v1/preview/options", {credentials:"same-origin"});
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    const body = await response.json(); publications = body.items || [];
    source.innerHTML = publications.length ? publications.map(p => `<option value="${esc(p.publication_id)}">${esc(p.source_name)} / ${esc(p.publisher)}</option>`).join("") : "<option value=''>尚无已发布候选 Preview</option>";
    status.textContent = publications.length ? "请选择单一来源查看；禁止全部来源合计。" : "尚未发布候选演示数据。请在 8121 完成人工 Preview 发布后刷新。";
    if (publications.length) await load();
  }
  function bars(target, groups) {
    const values = [...groups.values()]; const max = Math.max(1, ...values.map(x => x.value));
    target.innerHTML = `<div class="preview-bars">${values.map(x => `<div class="preview-bar" style="height:${Math.max(4, Math.round(x.value/max*195))}px" data-label="${esc(x.label)}: ${esc(x.value)}"></div>`).join("")}</div>`;
  }
  async function load() {
    if (!source.value) return;
    const params = new URLSearchParams({publication_id:source.value,page:"1",page_size:"250"});
    if (start.value) params.set("start_date",start.value); if (end.value) params.set("end_date",end.value);
    if (state.value) params.set("state_code",state.value); if (municipality.value) params.set("municipality_code",municipality.value);
    status.textContent = "正在读取候选 Preview…";
    const response = await fetch(`/api/v1/preview/series?${params}`, {credentials:"same-origin"});
    if (!response.ok) { status.textContent = `读取失败（HTTP ${response.status}）`; return; }
    const body = await response.json(); const m = body.metadata; const items = body.items || [];
    status.textContent = `已显示 ${items.length}/${body.total} 条 Observation；合计通知记录数 ${body.value_total}`;
    summary.innerHTML = `<article><b>来源</b><br>${esc(m.source_name)}</article><article><b>发布机构</b><br>${esc(m.publisher)}</article><article><b>指标</b><br>${esc(m.display_name_zh)} / ${esc(m.unit)}</article><article><b>日期范围</b><br>${esc(m.date_min)} 至 ${esc(m.date_max)}</article><article><b>Observation</b><br>${esc(body.total)}</article><article><b>合计通知记录数</b><br>${esc(body.value_total)}</article><article><b>数据层级</b><br>REVIEW_PREVIEW</article><article><b>训练资格</b><br>false</article><article><b>许可</b><br>${esc(m.license_status)}</article><article><b>范围</b><br>${esc(m.coverage_status)}</article><article><b>Artifact</b><br><code>${esc(String(m.artifact_sha256).slice(0,12))}</code></article><article><b>Snapshot</b><br><code>${esc(String(m.snapshot_id).slice(0,12))}</code></article>`;
    const byDate = new Map(), byGeo = new Map();
    for (const row of items) { const d=row.observation_date, g=`${row.state_code}/${row.municipality_code}`; byDate.set(d,{label:d,value:(byDate.get(d)?.value||0)+Number(row.value_numeric)}); byGeo.set(g,{label:g,value:(byGeo.get(g)?.value||0)+Number(row.value_numeric)}); }
    bars(trend, byDate); bars(geo, byGeo);
    quality.innerHTML = `<div>许可：${esc(m.license_status)}</div><div>覆盖：${esc(m.coverage_status)}</div><div>数据性质：derived_aggregation</div><div>国家汇总：false</div><div>不进入训练、预测或每日更新</div>`;
    const states=[...new Set(body.dimensions.map(x=>x.state_code))], municipalities=[...new Set(body.dimensions.map(x=>x.municipality_code))];
    const selectedState=state.value, selectedMunicipality=municipality.value;
    state.innerHTML="<option value=''>全部州代码</option>"+states.map(v=>`<option ${v===selectedState?'selected':''}>${esc(v)}</option>`).join(""); municipality.innerHTML="<option value=''>全部市代码</option>"+municipalities.map(v=>`<option ${v===selectedMunicipality?'selected':''}>${esc(v)}</option>`).join("");
    table.innerHTML = `<table class="preview-table"><thead><tr><th>通知日期</th><th>州代码</th><th>市代码</th><th>通知记录数</th><th>质量状态</th><th>源行集合哈希</th></tr></thead><tbody>${items.map(r=>`<tr><td>${esc(r.observation_date)}</td><td>${esc(r.state_code)}</td><td>${esc(r.municipality_code)}</td><td>${esc(r.value_numeric)}</td><td>${esc(r.quality_status)}</td><td><code>${esc(String(r.source_row_set_sha256).slice(0,12))}</code></td></tr>`).join("")}</tbody></table><p><a href="/preview/lineage/${esc(source.value)}">查看完整血缘</a></p>`;
  }
  async function switchTier() { const preview=tier.value==="REVIEW_PREVIEW"; panel.hidden=!preview; trusted.forEach(node=>node.hidden=preview); if(preview) await options(); }
  tier.addEventListener("change", () => switchTier().catch(e => status.textContent=`候选 Preview 初始化失败：${e.message}`));
  document.getElementById("p1-10-preview-apply").addEventListener("click", () => load().catch(e => status.textContent=`读取失败：${e.message}`));
  document.getElementById("p1-10-preview-clear").addEventListener("click", () => {start.value="";end.value="";state.value="";municipality.value="";load().catch(()=>{});});
  if (new URLSearchParams(location.search).get("data_tier") === "REVIEW_PREVIEW") tier.value="REVIEW_PREVIEW";
  switchTier().catch(e => status.textContent=`候选 Preview 初始化失败：${e.message}`);
})();
