from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
DASHBOARD_APP = PROJECT / "src/epidemic_core/dashboard/app.py"
STATIC_JS = PROJECT / "src/epidemic_core/dashboard/static/dashboard.prediction-demo.js"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe(value: str) -> str:
    return re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+", "[DATABASE_URL_REDACTED]", value).replace(str(PROJECT), "[project]")


def run(label: str, args: list[str], timeout: int = 120) -> dict[str, Any]:
    cp = subprocess.run(args, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    out = safe((cp.stdout or "") + "\n" + (cp.stderr or ""))
    if cp.returncode:
        raise RuntimeError(f"{label} failed: " + " | ".join(out.splitlines()[-12:]))
    return {"label": label, "returncode": cp.returncode, "output_sha256": sha_text(out), "tail": out.splitlines()[-30:]}


PREDICTION_SECTION = """<section id="prediction-demo-panel" class="prediction-demo" aria-labelledby="prediction-demo-title">
<h2 id="prediction-demo-title">Outbreak Onset Risk Prediction（演示）</h2>
<p class="preview-warning">DEMO ONLY / NOT FOR PUBLIC-HEALTH DECISIONS。真实 Trusted 数据当前不足以训练经验证的 onset 模型；此区块只展示 SAMPLE_DATA / PIPELINE_SMOKE_TEST 工程链路。</p>
<div id="prediction-demo-status" class="notice">正在读取预测演示产物…</div>
<div id="prediction-demo-summary" class="summary-grid"></div>
<div id="prediction-demo-table" class="table-box"></div>
</section>"""


PREDICTION_JS = r'''(() => {
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
'''


def patch_dashboard_app() -> dict[str, Any]:
    before = DASHBOARD_APP.read_text(encoding="utf-8")
    before_sha = sha_text(before)
    text_value = before
    if "prediction-demo-panel" not in text_value:
        marker = "</main><script src=\"/static/dashboard.p1-7ar-v1.js\" defer></script>"
        if marker not in text_value:
            raise RuntimeError("BLOCKED_DASHBOARD_MAIN_SCRIPT_MARKER_NOT_FOUND")
        text_value = text_value.replace("</main><script src=\"/static/dashboard.p1-7ar-v1.js\" defer></script>", PREDICTION_SECTION + "</main><script src=\"/static/dashboard.p1-7ar-v1.js\" defer></script>", 1)
    if "/static/dashboard.prediction-demo.js" not in text_value:
        text_value = text_value.replace(
            "</body>",
            '<script src="/static/dashboard.prediction-demo.js" defer></script></body>',
            1,
        )
    if 'def prediction_demo_js()' not in text_value:
        route_marker = '    @app.get("/login")\n'
        route = '''    @app.get("/static/dashboard.prediction-demo.js")
    def prediction_demo_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.prediction-demo.js", media_type="application/javascript")

'''
        text_value = text_value.replace(route_marker, route + route_marker, 1)
    if 'def prediction_demo_api(' not in text_value:
        api_marker = '    @app.get("/api/v1/options")\n'
        api_route = '''    @app.get("/api/v1/prediction-demo")
    def prediction_demo_api(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_data_visualization_prediction_engineering_loop" / "prediction_dashboard_demo_payload.json"
        if not payload_path.exists():
            return _json_response({"detail": "PREDICTION_DEMO_NOT_GENERATED"}, 404)
        return _json_response(json.loads(payload_path.read_text(encoding="utf-8")))

'''
        text_value = text_value.replace(api_marker, api_route + api_marker, 1)
    DASHBOARD_APP.write_text(text_value, encoding="utf-8")
    after_sha = sha_text(text_value)
    return {"path": str(DASHBOARD_APP.relative_to(PROJECT)), "before_sha256": before_sha, "after_sha256": after_sha, "changed": before_sha != after_sha}


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    app_change = patch_dashboard_app()
    before_js = sha_bytes(STATIC_JS.read_bytes()) if STATIC_JS.exists() else None
    STATIC_JS.write_text(PREDICTION_JS, encoding="utf-8")
    js_change = {"path": str(STATIC_JS.relative_to(PROJECT)), "before_sha256": before_js, "after_sha256": sha_bytes(STATIC_JS.read_bytes()), "changed": before_js != sha_bytes(STATIC_JS.read_bytes())}
    checks = [
        run("py_compile_dashboard_app", [os.environ.get("PYTHON_BIN", sys.executable), "-m", "py_compile", str(DASHBOARD_APP)], 120),
    ]
    if shutil.which("node"):
        checks.append(run("js_syntax", ["node", "--check", str(STATIC_JS)], 120))
    else:
        checks.append({"label": "js_syntax", "returncode": None, "status": "SKIPPED_NODE_NOT_AVAILABLE", "output_sha256": None, "tail": []})
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "DASHBOARD_PREDICTION_DEMO_PATCHED",
        "app_change": app_change,
        "static_js_change": js_change,
        "checks": checks,
        "database_writes": 0,
        "trusted_snapshot_promotion_human_decision_writes": 0,
    }
    (REPORT / "dashboard_prediction_demo_patch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "modified": [app_change["path"], js_change["path"]], "checks": len(checks)}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
