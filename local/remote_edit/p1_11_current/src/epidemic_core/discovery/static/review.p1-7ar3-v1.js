"use strict";

const one = (selector, root = document) => root.querySelector(selector);
const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const csrf = () => one('meta[name="csrf-token"]')?.content || "";

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("Content-Type", "application/json");
    headers.set("X-CSRF-Token", csrf());
  }
  const response = await fetch(path, {...options, headers, credentials: "same-origin"});
  const type = response.headers.get("content-type") || "";
  const body = type.startsWith("application/json") ? await response.json() : {error: "NON_JSON_RESPONSE"};
  if (response.status === 401) {
    location.href = "/login";
    throw new Error("登录已失效");
  }
  if (!response.ok) {
    const error = new Error(body.message || body.error || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function setupDiscoveryForm() {
  const form = one("#discovery-form");
  if (!form) return;
  const country = one("#discover-country");
  const disease = one("#discover-disease");
  const countryId = one("#country-id");
  const diseaseId = one("#disease-id");
  const summary = one("#discovery-confirmation p");
  const optionMap = (listId) => new Map(all(`#${listId} option`).map(item => [item.value, item.dataset.id]));
  const countries = optionMap("discover-country-list");
  const diseases = optionMap("discover-disease-list");
  const update = () => {
    countryId.value = countries.get(country.value) || "";
    diseaseId.value = diseases.get(disease.value) || "";
    const metrics = all('input[name="desired_metrics"]:checked', form).map(item => item.parentElement.textContent.trim());
    const tiers = all('input[name="preferred_source_tiers"]:checked', form).map(item => item.parentElement.textContent.trim());
    summary.textContent = countryId.value && diseaseId.value
      ? `国家：${country.value}；病种：${disease.value}；指标：${metrics.join("、") || "未选择"}；来源：${tiers.join("、") || "未选择"}。`
      : "请从 catalog 候选中选择国家和病种。";
  };
  form.addEventListener("input", update);
  form.addEventListener("submit", event => {
    update();
    if (!countryId.value || !diseaseId.value) {
      event.preventDefault();
      summary.textContent = "国家或病种不是正式 catalog 选项；任务尚未创建。";
      summary.parentElement.classList.add("error");
    }
  });
  update();
}

function setupAutoRefresh() {
  const toggle = one("#auto-refresh");
  if (!toggle) return;
  let timer = null;
  const schedule = () => {
    if (timer) window.clearTimeout(timer);
    if (toggle.checked) timer = window.setTimeout(() => location.reload(), 5000);
  };
  toggle.addEventListener("change", schedule);
  schedule();
}

function setupDetailRefresh() {
  if (!/^\/discoveries\/[0-9a-f-]+$/.test(location.pathname)) return;
  const terminalLabels = [
    "候选等待人工复核", "未找到合格候选", "仅发现重复来源", "策略阻断",
    "安全阻断", "许可阻断", "格式暂不支持", "请求字段无效", "任务失败"
  ];
  if (!terminalLabels.some(label => document.body.innerText.includes(label))) {
    window.setTimeout(() => location.reload(), 5000);
  }
}

const whitelists = {
  status: [["mapped", "用于入库"], ["raw-only", "仅保留原始证据"], ["ignored", "忽略"], ["unsupported", "暂不支持"]],
  canonical: [["", "不映射"], ["location_code", "标准地点代码"], ["period_start", "报告期开始"], ["period_end", "报告期结束"], ["value_numeric", "数值"], ["raw_value", "原始值"], ["granularity", "时间粒度"], ["metric_code", "指标代码"], ["source_unit", "来源单位"]],
  metric: [["", "不指定指标"], ["cases_new", "新增病例"], ["deaths_new", "新增死亡"], ["incidence_rate", "发病率"]],
  transform: [["identity", "保持原值"], ["parse_iso_date", "解析 ISO 日期"], ["parse_epiweek", "流行病学周转标准周"], ["parse_isoweek", "ISO 周转标准周"], ["parse_month", "月份转标准月"], ["country_name_to_iso3", "国家名转 ISO3"], ["numeric_count", "解析计数"], ["numeric_rate", "解析比率"], ["explicit_null_tokens", "显式缺失标记"]]
};

function selectControl(kind, current, label) {
  const control = document.createElement("select");
  control.className = "mapping-select";
  control.dataset.kind = kind;
  control.setAttribute("aria-label", label);
  for (const [value, text] of whitelists[kind]) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = text;
    item.selected = value === current;
    control.appendChild(item);
  }
  return control;
}

function mappingPayload(table) {
  return all("tbody tr", table).map(row => {
    const controls = Object.fromEntries(all("select", row).map(control => [control.dataset.kind, control.value]));
    if (controls.status !== "mapped") return null;
    return {
      source_field: row.dataset.sourceField,
      canonical_field: controls.canonical,
      metric_code: controls.metric,
      transform: controls.transform || "identity",
      source_unit: row.dataset.sourceUnit || "",
      target_unit: row.dataset.targetUnit || "",
      confidence: Number(row.dataset.confidence || "0.5"),
      warning: row.dataset.warning || ""
    };
  }).filter(Boolean);
}

function setupMapping() {
  const table = one("#mapping-table");
  const button = one("#save-mapping");
  if (!table || !button) return;
  const original = new Map();
  for (const row of all("tbody tr", table)) {
    const cells = all("td", row);
    original.set(row.dataset.sourceField, JSON.stringify({status: row.dataset.mappingStatus, canonical: row.dataset.canonical, metric: row.dataset.metric, transform: row.dataset.transform}));
    const status = cells[3];
    const standard = cells[4];
    const transform = cells[5];
    status.textContent = "";
    status.appendChild(selectControl("status", row.dataset.mappingStatus, `${row.dataset.sourceField} 建议处理`));
    standard.textContent = "";
    standard.appendChild(selectControl("canonical", row.dataset.canonical, `${row.dataset.sourceField} 标准字段`));
    standard.appendChild(selectControl("metric", row.dataset.metric, `${row.dataset.sourceField} 指标`));
    transform.insertBefore(selectControl("transform", row.dataset.transform || "identity", `${row.dataset.sourceField} 转换`), transform.firstChild);
  }
  const updateDiff = () => {
    const changed = all("tbody tr", table).filter(row => {
      const controls = Object.fromEntries(all("select", row).map(control => [control.dataset.kind, control.value]));
      return original.get(row.dataset.sourceField) !== JSON.stringify(controls);
    });
    one("#mapping-diff p").textContent = changed.length
      ? `将创建新 revision：${changed.map(row => row.dataset.sourceField).join("、")} 共 ${changed.length} 个字段发生变化。`
      : "尚未修改；保存会创建新 mapping revision，不覆盖历史。";
  };
  table.addEventListener("change", updateDiff);
  button.addEventListener("click", async () => {
    const result = one("#mapping-result");
    result.textContent = "正在保存新的 mapping revision…";
    try {
      const data = await api(`/api/v1/review-cases/${encodeURIComponent(button.dataset.case)}/mapping`, {
        method: "POST",
        body: JSON.stringify({expected_revision: Number(button.dataset.revision), fields: mappingPayload(table)})
      });
      result.textContent = `已保存 revision ${data.new_revision}；未写入正式数据。`;
      window.setTimeout(() => location.reload(), 1200);
    } catch (error) {
      result.textContent = error.status === 409 ? "候选状态已变化，请刷新后重试。" : `保存失败：${error.message}`;
      result.className = "error";
    }
  });
}

function setupDecisionReason() {
  const template = one("#reason-template");
  const reason = one("#decision-reason");
  if (!template || !reason) return;
  template.addEventListener("change", () => {
    if (!reason.value.trim()) reason.value = template.value;
  });
}

function setupPreviewColumns() {
  const heading = all("h2").find(item => item.textContent.includes("原始数据与清洗结果预览"));
  const section = heading?.closest("section");
  const table = one("table", section);
  if (!table) return;
  const selector = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = "列选择器";
  selector.appendChild(legend);
  const columns = [[3, "报告期"], [4, "地理名称/代码"], [5, "病例"], [6, "死亡"], [7, "单位"]];
  for (const [index, label] of columns) {
    const wrapper = document.createElement("label");
    const control = document.createElement("input");
    control.type = "checkbox";
    control.checked = true;
    control.value = String(index);
    control.addEventListener("change", () => {
      for (const row of all("tr", table)) {
        if (row.children[index]) row.children[index].hidden = !control.checked;
      }
    });
    wrapper.append(control, document.createTextNode(label));
    selector.appendChild(wrapper);
  }
  const tableBox = table.closest(".table-box");
  if (!tableBox || !tableBox.parentElement || one(".preview-column-selector", section)) return;
  selector.className = "preview-column-selector";
  tableBox.parentElement.insertBefore(selector, tableBox);
}

setupDiscoveryForm();
setupAutoRefresh();
setupDetailRefresh();
setupMapping();
setupDecisionReason();
setupPreviewColumns();
