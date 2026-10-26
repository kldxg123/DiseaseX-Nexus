(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));

  function setStatus(text, error) {
    const el = $("status");
    el.textContent = text;
    el.className = error ? "notice error" : "notice";
  }

  function renderSummary(payload) {
    const c = payload.coverage;
    $("summary").innerHTML = [
      ["Validated 已验证", c.validated],
      ["Pending validation 待验证", c.pending_validation],
      ["Metadata missing 元数据缺失", c.metadata_missing],
      ["Target weeks 目标周", c.target_weeks],
      ["Range 范围", `${c.range_start} → ${c.range_end}`],
      ["Trusted/final", "No / Preview only"],
    ]
      .map(([k, v]) => `<article><strong>${v}</strong><span>${k}</span></article>`)
      .join("");
  }

  function renderChart(payload) {
    const svg = $("chart");
    const points = payload.coverage_matrix;
    const valid = points.filter((p) => p.coverage_status === "validated" && p.current_week_numeric !== null);
    const w = 980;
    const h = 360;
    const pad = { l: 54, r: 18, t: 24, b: 62 };
    const maxY = Math.max(...valid.map((p) => Number(p.current_week_numeric)), 1);
    const x = (i) => pad.l + (i * (w - pad.l - pad.r)) / Math.max(points.length - 1, 1);
    const y = (v) => h - pad.b - (Number(v) * (h - pad.t - pad.b)) / maxY;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const grid = [0, 0.25, 0.5, 0.75, 1]
      .map((t) => {
        const yy = pad.t + t * (h - pad.t - pad.b);
        const val = Math.round(maxY * (1 - t));
        return `<line x1="${pad.l}" y1="${yy}" x2="${w - pad.r}" y2="${yy}" class="grid"/><text x="8" y="${yy + 4}" class="axis">${val}</text>`;
      })
      .join("");
    let segments = "";
    let current = [];
    points.forEach((p, i) => {
      if (p.coverage_status === "validated" && p.current_week_numeric !== null) {
        current.push(`${x(i)},${y(p.current_week_numeric)}`);
      } else if (current.length) {
        if (current.length > 1) segments += `<polyline points="${current.join(" ")}" class="line"/>`;
        current = [];
      }
    });
    if (current.length > 1) segments += `<polyline points="${current.join(" ")}" class="line"/>`;
    const dots = points
      .map((p, i) => {
        if (p.coverage_status === "validated" && p.current_week_numeric !== null) {
          return `<circle cx="${x(i)}" cy="${y(p.current_week_numeric)}" r="4" class="dot"><title>${p.week} ${p.current_week_raw}</title></circle>`;
        }
        const cls = p.coverage_status === "metadata_missing" ? "missing" : "pending";
        return `<line x1="${x(i)}" y1="${h - pad.b + 6}" x2="${x(i)}" y2="${h - pad.b + 18}" class="${cls}"><title>${p.week} ${p.coverage_status}</title></line>`;
      })
      .join("");
    const ticks = points
      .filter((_, i) => i % 13 === 0 || i === points.length - 1)
      .map((p) => {
        const i = points.indexOf(p);
        return `<text x="${x(i)}" y="${h - 20}" class="axis tick" transform="rotate(35 ${x(i)} ${h - 20})">${p.week}</text>`;
      })
      .join("");
    svg.innerHTML = `${grid}<line x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}" class="axis-line"/>${segments}${dots}${ticks}`;
  }

  function renderTable(payload) {
    const rows = payload.coverage_matrix
      .filter((p) => p.coverage_status === "validated")
      .sort((a, b) => a.week.localeCompare(b.week));
    $("table").innerHTML =
      "<table><thead><tr><th>周号</th><th>week-ending date</th><th>原始值</th><th>CDC PID</th><th>验证状态</th></tr></thead><tbody>" +
      rows
        .map(
          (r) =>
            `<tr><td>${fmt(r.week)}</td><td>${fmt(r.week_ending_date)}</td><td>${fmt(r.current_week_raw)}</td><td>${fmt(r.cdc_pid)}</td><td><span class="badge">${fmt(r.validation_status)}</span></td></tr>`
        )
        .join("") +
      "</tbody></table>";
  }

  function renderGaps(payload) {
    const pending = payload.coverage_matrix.filter((p) => p.coverage_status === "pending_validation").map((p) => p.week);
    const missing = payload.coverage_matrix.filter((p) => p.coverage_status === "metadata_missing").map((p) => p.week);
    $("gaps").innerHTML = `<h2>104 周覆盖状态</h2>
      <p><strong>validated = ${payload.coverage.validated}</strong>, pending validation = ${payload.coverage.pending_validation}, metadata missing = ${payload.coverage.metadata_missing}</p>
      <details><summary>待验证 ${pending.length} 周</summary><p>${pending.join(", ")}</p></details>
      <details><summary>元数据缺失 ${missing.length} 周</summary><p>${missing.join(", ")}</p></details>`;
  }

  async function main() {
    try {
      setStatus("正在读取美国 Legionellosis preview 数据…");
      const res = await fetch("/api/v1/legionellosis-preview", { credentials: "same-origin" });
      if (!res.ok) throw new Error(`HTTP_${res.status}`);
      const payload = await res.json();
      $("notice").textContent = payload.preview_notice;
      renderSummary(payload);
      renderChart(payload);
      renderTable(payload);
      renderGaps(payload);
      $("csvLink").href = "/api/v1/legionellosis-preview.csv";
      setStatus(`已加载 ${payload.coverage.validated} 个已验证周；未补零、未插值、未预测。`);
    } catch (err) {
      setStatus(`加载失败：${err.message || err}`, true);
    }
  }
  window.addEventListener("DOMContentLoaded", main);
})();
