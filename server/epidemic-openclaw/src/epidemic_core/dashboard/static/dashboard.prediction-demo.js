(() => {
  const statusEl = document.getElementById("prediction-demo-status");
  const summaryEl = document.getElementById("prediction-demo-summary");
  const tableEl = document.getElementById("prediction-demo-table");
  if (!statusEl || !summaryEl || !tableEl) return;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  fetch("/api/v1/prediction-demo", { credentials: "same-origin" })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP_${response.status}`);
      return response.json();
    })
    .then((payload) => {
      statusEl.textContent = `${payload.status || "UNKNOWN"} / ${payload.substatus || ""}`;
      const predictions = Array.isArray(payload.predictions) ? payload.predictions : [];
      summaryEl.innerHTML = [
        ["模式", payload.mode],
        ["警告", payload.warning],
        ["预测条数", predictions.length],
        ["数据边界", "review/candidate rows used = 0"],
      ].map(([k, v]) => `<div class="stat"><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join("");
      const rows = predictions.slice(0, 12).map((row) => `<tr><td>${esc(row.region)}</td><td>${esc(row.prediction_origin)}</td><td>${esc(row.target_week_start)}</td><td>${esc(row.risk_score)}</td><td>${esc(row.risk_level)}</td><td>${esc(row.data_sufficiency)}</td></tr>`).join("");
      tableEl.innerHTML = `<table><caption>Prediction smoke-test output</caption><thead><tr><th>Region</th><th>Origin</th><th>Target week</th><th>Risk score</th><th>Risk level</th><th>Data sufficiency</th></tr></thead><tbody>${rows || '<tr><td colspan="6">暂无预测演示产物</td></tr>'}</tbody></table>`;
    })
    .catch((err) => {
      statusEl.textContent = `Prediction demo unavailable: ${err.message}`;
    });
})();
