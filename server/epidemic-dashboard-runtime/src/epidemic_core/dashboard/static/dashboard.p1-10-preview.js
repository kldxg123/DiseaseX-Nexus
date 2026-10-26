(() => {
  "use strict";
  const INTERFACE_VERSION = "p1-10r-v1";
  const tier = document.getElementById("p1-10-data-tier");
  const panel = document.getElementById("p1-10-preview-panel");
  if (!tier || !panel) return;
  const trusted = [document.querySelector(".daily-update"), document.querySelector("main > .filters"), document.getElementById("empty"), document.getElementById("summary"), document.querySelector("main > .charts")].filter(Boolean);
  const source = document.getElementById("p1-10-preview-source");
  const country = document.getElementById("p1-10-preview-country");
  const disease = document.getElementById("p1-10-preview-disease");
  const metric = document.getElementById("p1-10-preview-metric");
  const snapshot = document.getElementById("p1-10-preview-snapshot");
  const state = document.getElementById("p1-10-preview-state");
  const municipality = document.getElementById("p1-10-preview-municipality");
  const start = document.getElementById("p1-10-preview-start");
  const end = document.getElementById("p1-10-preview-end");
  const sort = document.getElementById("p1-10-preview-sort");
  const status = document.getElementById("p1-10-preview-status");
  const summary = document.getElementById("p1-10-preview-summary");
  const table = document.getElementById("p1-10-preview-table");
  const trend = document.getElementById("p1-10-preview-trend");
  const geo = document.getElementById("p1-10-preview-geo");
  const quality = document.getElementById("p1-10-preview-quality");
  const errorCard = document.getElementById("p1-10-preview-error");
  const errorCode = document.getElementById("p1-10-preview-error-code");
  const requestId = document.getElementById("p1-10-preview-request-id");
  const pageLabel = document.getElementById("p1-10-preview-page");
  const previous = document.getElementById("p1-10-preview-prev");
  const next = document.getElementById("p1-10-preview-next");
  let publications = [];
  let page = 1;
  let pageCount = 1;
  let activeRequest = null;
  const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[character]));

  async function requestJson(url) {
    if (activeRequest) activeRequest.abort();
    activeRequest = new AbortController();
    const response = await fetch(url, {credentials:"same-origin",headers:{"X-Client-Interface":INTERFACE_VERSION},signal:activeRequest.signal});
    let body = {};
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) {
      const failure = new Error(body.error_code || `HTTP_${response.status}`);
      failure.code = body.error_code || `HTTP_${response.status}`;
      failure.requestId = body.request_id || response.headers.get("X-Request-ID") || "unknown";
      failure.status = response.status;
      throw failure;
    }
    return {body, requestId: response.headers.get("X-Request-ID") || "unknown"};
  }

  function clearVisuals() {
    summary.innerHTML = "";
    table.innerHTML = "";
    trend.innerHTML = "";
    geo.innerHTML = "";
    quality.innerHTML = "";
    page = 1;
    pageCount = 1;
    pageLabel.textContent = "第 1/1 页";
    previous.disabled = true;
    next.disabled = true;
  }

  function showError(error) {
    if (error && error.name === "AbortError") return;
    clearVisuals();
    const code = error.code || "UNKNOWN_INTERNAL_ERROR";
    const rid = error.requestId || "unknown";
    errorCode.textContent = code;
    requestId.textContent = rid;
    errorCard.hidden = false;
    status.textContent = `候选 Preview 暂不可用（${code}，request ${rid}）`;
  }

  function selectedPublication() {
    return publications.find((item) => String(item.publication_id) === source.value) || null;
  }

  function updatePublicationSelectors() {
    const item = selectedPublication();
    country.innerHTML = item ? `<option>${esc(item.country_iso2)}</option>` : "<option>—</option>";
    disease.innerHTML = item ? `<option>${esc(item.disease_slug)}</option>` : "<option>—</option>";
    metric.innerHTML = item ? `<option>${esc(item.display_name_zh)} (${esc(item.metric_code)})</option>` : "<option>—</option>";
    snapshot.innerHTML = item ? `<option value="${esc(item.snapshot_id)}">${esc(String(item.snapshot_id).slice(0,12))} · ${esc(item.date_min)}–${esc(item.date_max)}</option>` : "<option>—</option>";
  }

  async function options() {
    errorCard.hidden = true;
    status.textContent = "正在初始化候选 Preview…";
    const {body} = await requestJson("/api/v1/preview/options");
    publications = body.items || [];
    source.innerHTML = publications.length
      ? publications.map((item) => `<option value="${esc(item.publication_id)}">${esc(item.source_name)} / ${esc(item.publisher)}</option>`).join("")
      : "<option value=''>尚无已发布候选 Preview</option>";
    updatePublicationSelectors();
    if (body.state === "EMPTY" || !publications.length) {
      clearVisuals();
      status.textContent = body.message || "当前尚未发布候选演示数据。请先在 Source Review Console 完成复核并生成候选预览。";
      return;
    }
    status.textContent = body.message || "请选择单一来源查看；禁止全部来源合计。";
    await load();
  }

  function bars(target, items) {
    const values = items || [];
    if (!values.length) { target.innerHTML = '<p class="notice">当前筛选无可视化记录。</p>'; return; }
    const maximum = Math.max(1, ...values.map((item) => Number(item.value)));
    target.innerHTML = `<div class="preview-bars">${values.map((item) => `<div class="preview-bar" style="height:${Math.max(4, Math.round(Number(item.value) / maximum * 195))}px" title="${esc(item.label)}: ${esc(item.value)}"><span>${esc(item.label)}</span></div>`).join("")}</div>`;
  }

  async function load() {
    if (!source.value) return;
    errorCard.hidden = true;
    const params = new URLSearchParams({publication_id:source.value,page:String(page),page_size:"50",sort:sort.value});
    if (start.value) params.set("start_date",start.value);
    if (end.value) params.set("end_date",end.value);
    if (state.value) params.set("state_code",state.value);
    if (municipality.value) params.set("municipality_code",municipality.value);
    status.textContent = "正在读取候选 Preview…";
    const {body} = await requestJson(`/api/v1/preview/series?${params}`);
    const metadata = body.metadata;
    const items = body.items || [];
    pageCount = Number(body.page_count || 1);
    page = Number(body.page || 1);
    status.textContent = `第 ${page}/${pageCount} 页；共 ${body.total} 条 Observation；合计通知记录数 ${body.value_total}`;
    summary.innerHTML = `<article><b>来源</b><br>${esc(metadata.source_name)}</article><article><b>发布机构</b><br>${esc(metadata.publisher)}</article><article><b>指标</b><br>${esc(metadata.display_name_zh)} / ${esc(metadata.unit)}</article><article><b>日期范围</b><br>${esc(metadata.date_min)} 至 ${esc(metadata.date_max)}</article><article><b>地理范围</b><br>${esc(metadata.state_count)} 州 / ${esc(metadata.municipality_count)} 市</article><article><b>Observation</b><br>${esc(body.total)}</article><article><b>合计通知记录数</b><br>${esc(body.value_total)}</article><article><b>数据性质</b><br>${esc(metadata.data_nature)}</article><article><b>训练资格</b><br>false</article><article><b>许可</b><br>${esc(metadata.license_status)}</article><article><b>覆盖</b><br>${esc(metadata.coverage_status)}</article><article><b>Artifact</b><br><code>${esc(String(metadata.artifact_sha256).slice(0,12))}</code></article><article><b>Preview Snapshot</b><br><code>${esc(String(metadata.snapshot_id).slice(0,12))}</code></article>`;
    bars(trend, body.trend);
    bars(geo, body.geography);
    const qualityRows = (body.quality_distribution || []).map((item) => `<li>${esc(item.label)}：${esc(item.value)}</li>`).join("");
    quality.innerHTML = `<div>许可：${esc(metadata.license_status)}</div><div>覆盖：${esc(metadata.coverage_status)}</div><div>数据性质：derived_aggregation</div><div>production_usable=false</div><div>training_eligible=false</div><div>national_aggregate=false</div><div>不进入训练、预测或每日更新</div><ul>${qualityRows}</ul>`;
    const states = [...new Set(body.dimensions.map((item) => item.state_code))];
    const municipalities = [...new Set(body.dimensions.map((item) => item.municipality_code))];
    const selectedState = state.value;
    const selectedMunicipality = municipality.value;
    state.innerHTML = "<option value=''>全部州代码</option>" + states.map((value) => `<option value="${esc(value)}" ${value === selectedState ? "selected" : ""}>${esc(value)}</option>`).join("");
    municipality.innerHTML = "<option value=''>全部市代码</option>" + municipalities.map((value) => `<option value="${esc(value)}" ${value === selectedMunicipality ? "selected" : ""}>${esc(value)}</option>`).join("");
    table.innerHTML = `<table class="preview-table"><thead><tr><th>通知日期</th><th>州代码</th><th>市代码</th><th>通知记录数</th><th>质量状态</th><th>源行集合哈希</th></tr></thead><tbody>${items.map((row) => `<tr><td>${esc(row.observation_date)}</td><td>${esc(row.state_code)}</td><td>${esc(row.municipality_code)}</td><td>${esc(row.value_numeric)}</td><td>${esc(row.quality_status)}</td><td><code>${esc(String(row.source_row_set_sha256).slice(0,12))}</code></td></tr>`).join("")}</tbody></table><p><a href="/preview/lineage/${esc(source.value)}">查看完整血缘</a></p>`;
    pageLabel.textContent = `第 ${page}/${pageCount} 页`;
    previous.disabled = page <= 1;
    next.disabled = page >= pageCount;
  }

  async function switchTier() {
    const preview = tier.value === "REVIEW_PREVIEW";
    panel.hidden = !preview;
    trusted.forEach((node) => { node.hidden = preview; });
    if (preview) await options();
  }

  tier.addEventListener("change", () => switchTier().catch(showError));
  source.addEventListener("change", () => { page = 1; updatePublicationSelectors(); load().catch(showError); });
  document.getElementById("p1-10-preview-apply").addEventListener("click", () => { page = 1; load().catch(showError); });
  document.getElementById("p1-10-preview-clear").addEventListener("click", () => { start.value = ""; end.value = ""; state.value = ""; municipality.value = ""; sort.value = "date_asc"; page = 1; load().catch(showError); });
  document.getElementById("p1-10-preview-retry").addEventListener("click", () => options().catch(showError));
  previous.addEventListener("click", () => { if (page > 1) { page -= 1; load().catch(showError); } });
  next.addEventListener("click", () => { if (page < pageCount) { page += 1; load().catch(showError); } });
  if (new URLSearchParams(location.search).get("data_tier") === "REVIEW_PREVIEW") tier.value = "REVIEW_PREVIEW";
  switchTier().catch(showError);
})();
