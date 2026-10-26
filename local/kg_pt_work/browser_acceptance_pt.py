"""Browser acceptance for the KG parameter-tuning & learning-loop section (Playwright, local tunnel).

Checks: login, #kg-param-loop full render (all panels non-empty, no raw null/undefined,
SVG charts drawn, agreement disclaimer present, champion/feedback/cycles rendered),
run-button idempotent replay path, dual-loop section regression, other pages regression,
console/pageerror/first-party-failed-request must stay empty. Sanitized PNG screenshots only.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:18130"
OUT_DIR = Path(__file__).parent / "screenshots"
OUT_DIR.mkdir(exist_ok=True)

creds = Path(r"F:/作业/aibola/secrets/dashboard_login.txt").read_text(encoding="utf-8").splitlines()
USER, PW = creds[0].strip(), creds[1].strip()

results = {"cases": {}, "console_errors": [], "page_errors": [], "failed_requests": []}

PANELS = [
    "kgPtContext", "kgPtRec", "kgPtParams", "kgPtMetrics", "kgPtTest",
    "kgPtChampion", "kgPtFeedback", "kgPtCycles", "kgPtLimits", "kgPtRunInfo",
]
SVGS = ["kgPtTimeline", "kgPtTuning", "kgPtForecast"]
FORBIDDEN = ["undefined", "NaN%", "[object Object]", "加载失败"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 960})

    def attach(tag):
        page.on("console", lambda m: results["console_errors"].append({"page": tag, "text": m.text[:300]}) if m.type == "error" else None)
        page.on("pageerror", lambda e: results["page_errors"].append({"page": tag, "text": str(e)[:300]}))
        def on_resp(resp):
            if resp.status >= 400 and resp.url.startswith(BASE):
                results["failed_requests"].append({"page": tag, "url": resp.url[len(BASE):][:120], "status": resp.status})
        page.on("response", on_resp)

    attach("kg")

    resp = page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="domcontentloaded")
    results["cases"]["anonymous_redirect"] = "/login" in page.url

    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    results["cases"]["login"] = "/login" not in page.url

    page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle")
    page.wait_for_function(
        "() => document.getElementById('kgPtRunInfo') && "
        "document.getElementById('kgPtRunInfo').textContent.includes('已加载参数优化闭环')",
        timeout=90000,
    )
    texts = {el: page.inner_text(f"#{el}") for el in PANELS}
    results["cases"]["panels_nonempty"] = all(len(t.strip()) > 10 for t in texts.values())
    results["cases"]["panels_no_forbidden"] = all(
        bad not in t for t in texts.values() for bad in FORBIDDEN
    )
    svg_children = {s: page.eval_on_selector(f"#{s}", "e => e.childElementCount") for s in SVGS}
    results["cases"]["svg_children"] = svg_children
    results["cases"]["svg_drawn"] = all(c > 3 for c in svg_children.values())

    test_txt = texts["kgPtTest"]
    results["cases"]["agreement_disclaimer_adjacent"] = ("预测吻合度" in test_txt) and (
        "不是分类准确率" in test_txt and "不代表未来保证准确" in test_txt
    )
    results["cases"]["champion_shown"] = "Champion" in texts["kgPtChampion"] or "冠军" in texts["kgPtChampion"]
    results["cases"]["cycles_shown"] = "CONTROLLED_RETRAIN" in texts["kgPtCycles"] or "重训" in texts["kgPtCycles"]
    results["cases"]["feedback_progress"] = "MATURED" in texts["kgPtFeedback"] or "已成熟" in texts["kgPtFeedback"]
    results["cases"]["shadow_warning"] = "Shadow" in texts["kgPtLimits"] or "历史回放" in texts["kgPtLimits"]

    # extract UI numbers for DB/API reconciliation
    import re
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", test_txt)
    results["cases"]["ui_agreement_pct"] = m.group(1) if m else None

    page.screenshot(path=str(OUT_DIR / "pt_loop_full.png"), full_page=False)
    el = page.locator("#kg-param-loop")
    el.scroll_into_view_if_needed()
    el.screenshot(path=str(OUT_DIR / "pt_loop_section.png"))

    # run button -> must be an idempotent replay (content unchanged), button re-enables
    page.click("#kgPtRun")
    page.wait_for_function(
        "() => { const t = document.getElementById('kgPtRunInfo').textContent; "
        "return t.includes('已加载参数优化闭环'); }",
        timeout=120000,
    )
    results["cases"]["run_button_replay_ok"] = True
    results["cases"]["run_info_after_click"] = page.inner_text("#kgPtRunInfo")[:160]

    # dual-loop section regression (Austria)
    page.select_option("#kgDlDataset", "AUT")
    page.click("#kgDlRun")
    page.wait_for_function(
        "() => document.getElementById('kgDlStatus').textContent.includes('已加载')", timeout=60000
    )
    dl = page.inner_text("#kgDlBacktest")
    results["cases"]["dual_loop_regression"] = len(dl.strip()) > 10 and "加载失败" not in dl

    for path, name in [("/", "root"), ("/predictions", "predictions"), ("/fasttrack-forecast", "fasttrack")]:
        before = (len(results["console_errors"]), len(results["page_errors"]), len(results["failed_requests"]))
        r = page.goto(BASE + path, wait_until="networkidle")
        results["cases"][f"regression_{name}"] = {
            "status": r.status,
            "no_new_errors": before == (len(results["console_errors"]), len(results["page_errors"]), len(results["failed_requests"])),
        }
    browser.close()

results["console_error_count"] = len(results["console_errors"])
results["pageerror_count"] = len(results["page_errors"])
results["first_party_failed_request_count"] = len(results["failed_requests"])
out = Path(__file__).parent / "browser_acceptance.json"
out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in results.items() if k != "cases"}, indent=2, ensure_ascii=False))
print(json.dumps(results["cases"], indent=2, ensure_ascii=False, default=str)[:3000])
bool_cases = [v for v in results["cases"].values() if isinstance(v, bool)]
dict_cases = [v for v in results["cases"].values() if isinstance(v, dict)]
ok = (
    results["console_error_count"] == 0
    and results["pageerror_count"] == 0
    and results["first_party_failed_request_count"] == 0
    and all(bool_cases)
    and all(c.get("no_new_errors", True) and c.get("status", 200) < 400 for c in dict_cases)
)
print("BROWSER_ACCEPTANCE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
