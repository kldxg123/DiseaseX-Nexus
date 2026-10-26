
const state = { runId: null, scope: "LATEST", includeStale: false, limit: 50, offset: 0 };
const $ = (id) => document.getElementById(id);
function fmt(x){ return x === null || x === undefined || x === "" ? "待回填" : x; }
function num(x){ return x === null || x === undefined ? "待回填" : Number(x).toFixed(4); }
async function getJson(url){
  const r = await fetch(url, {credentials:"same-origin"});
  const text = await r.text();
  if(!r.ok) throw new Error(`${r.status} ${text}`);
  return text ? JSON.parse(text) : {};
}
function renderMetrics(run){
  const m = run.metrics?.logistic_regression || {};
  const warnings = (run.warnings || []).map(w => w.code || w).join(", ");
  $("runSummary").innerHTML = [
    ["Parent run ID", run.run_id],
    ["Model key", "cpu_logistic_regression_no_class_weight_no_calibration_v1"],
    ["Legacy model artifact SHA", run.model_sha256],
    ["Portable model artifact SHA", run.model_config?.p1_17a_r?.portable_model_artifact_sha256 || "见修复报告"],
    ["Model-state SHA", run.model_config?.p1_17a_r?.model_state_sha256 || "见修复报告"],
    ["Source data through", run.data_through],
    ["Lag weeks", "1-8"],
    ["Decision threshold", "0.5"],
    ["Generated at", run.completed_at || run.generated_at],
    ["Average Precision (AP)", num(m.average_precision ?? m.pr_auc)],
    ["Brier", num(m.brier)],
    ["ECE", num(m.ece)],
    ["TP/FP/TN/FN", `${m.tp}/${m.fp}/${m.tn}/${m.fn}`],
    ["Precision / Recall / F1 @0.5", `${fmt(m.precision)} / ${fmt(m.recall)} / ${fmt(m.f1)}`],
    ["Warnings", warnings || "retrospective/vintage limitation"],
  ].map(([k,v])=>`<div class="metric"><span>${k}</span><strong>${fmt(v)}</strong></div>`).join("");
}
function renderTable(rows){
  if(!rows.length){ $("resultsTable").innerHTML = `<div class="empty">当前筛选条件下没有结果。</div>`; return; }
  $("resultsTable").innerHTML = `<table><thead><tr><th>国家</th><th>监测来源</th><th>序列</th><th>预测起点</th><th>目标周</th><th>风险</th><th>Alert</th><th>实际标签</th><th>Freshness</th></tr></thead><tbody>${
    rows.map(r=>`<tr data-row-id="${r.row_id}"><td>${r.country_iso3}</td><td>${r.surveillance_origin}</td><td>${r.series_key}</td><td>${r.prediction_origin}</td><td>${r.target_week}</td><td class="risk">${Number(r.risk_score).toFixed(6)}</td><td>${r.predicted_alert}</td><td>${fmt(r.actual_label)}</td><td class="freshness">${fmt(r.freshness_status)}</td></tr>`).join("")
  }</tbody></table>`;
}
function renderChart(rows){
  const svg = $("riskChart"); svg.innerHTML = "";
  const pts = rows.slice().reverse();
  if(!pts.length) return;
  const w = svg.clientWidth || 900, h = 260, pad = 28;
  const max = Math.max(...pts.map(r=>Number(r.risk_score)), 0.5, 1);
  const step = (w-2*pad)/Math.max(1, pts.length-1);
  const y = (v)=> h-pad - (h-2*pad)*(v/max);
  const path = pts.map((r,i)=>`${i?'L':'M'}${pad+i*step},${y(Number(r.risk_score))}`).join(" ");
  svg.innerHTML = `<path d="${path}" fill="none" stroke="#2454a6" stroke-width="2"/><line x1="${pad}" x2="${w-pad}" y1="${y(0.5)}" y2="${y(0.5)}" stroke="#d24" stroke-dasharray="5 5"/><text x="${pad}" y="${y(0.5)-5}" fill="#d24">0.5 threshold</text>`;
}
async function load(){
  try{
    $("status").textContent = "加载中...";
    const runs = await getJson("/api/v1/predictions/runs?limit=20");
    if(!runs.items.length){ $("status").innerHTML = `<div class="empty">暂无 COMPLETE run。</div>`; return; }
    const select = $("runSelect");
    select.innerHTML = runs.items.map(r=>`<option value="${r.run_id}">${r.completed_at || r.generated_at} · ${r.run_id.slice(0,8)}</option>`).join("");
    if(!state.runId) state.runId = runs.items[0].run_id;
    select.value = state.runId;
    const run = await getJson(`/api/v1/predictions/runs/${state.runId}`);
    renderMetrics(run);
    const stale = $("includeStale").checked ? "true" : "false";
    const url = `/api/v1/predictions/results?run_id=${state.runId}&scope=${state.scope}&include_stale=${stale}&limit=${state.limit}&offset=${state.offset}`;
    const res = await getJson(url);
    $("status").textContent = `共 ${res.total} 条；当前页面 ${res.items.length} 条；排序：${res.order_by}；模式：${state.scope} ${stale === "true" ? "包含陈旧结果" : "仅当前下一周预测"}`;
    renderTable(res.items);
    renderChart(res.items);
  }catch(e){ $("status").innerHTML = `<span class="error">API error: ${e.message}</span>`; }
}
window.addEventListener("DOMContentLoaded", ()=>{
  $("scopeSelect").addEventListener("change", e=>{ state.scope=e.target.value; state.offset=0; load(); });
  $("runSelect").addEventListener("change", e=>{ state.runId=e.target.value; state.offset=0; load(); });
  $("includeStale").addEventListener("change", e=>{ state.includeStale=e.target.checked; state.offset=0; load(); });
  $("reloadBtn").addEventListener("click", load);
  load();
});
