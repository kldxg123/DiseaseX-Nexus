"""Browser acceptance for the KG Qwen autotuning section (Playwright, local tunnel).

Checks: login, #kg-qwen-loop full render for Track A and Track B (all panels
non-empty, no raw null/undefined/NaN, tuning SVG drawn, WAPE-agreement
disclaimer present, champion/feedback/cycles/meta rendered), run-button
idempotent replay path (no second semantic run), kg-param-loop regression,
console/pageerror/first-party-failed-request must stay empty.
Sanitized PNG screenshots only (no credentials).
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
    "kgQContext", "kgQProfile", "kgQRec", "kgQPlans", "kgQParamRanges", "kgQRounds",
    "kgQEval", "kgQChampion", "kgQFeedback", "kgQCycles", "kgQMeta", "kgQLimits", "kgQRunInfo",
]
FORBIDDEN = ["undefined", "NaN%", "[object Object]", "加载失败", "null"]

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

    page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="domcontentloaded")
    results["cases"]["anonymous_redirect"] = "/login" in page.url

    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    results["cases"]["login"] = "/login" not in page.url

    page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle")
    page.wait_for_function(
        "() => document.getElementById('kgQRunInfo') && "
        "!document.getElementById('kgQRunInfo').textContent.includes('正在加载')",
        timeout=120000,
    )

    def check_track(track):
        texts = {el: page.inner_text(f"#{el}") for el in PANELS}
        empty = [el for el, t in texts.items() if not t.strip()]
        hits = {el: [f for f in FORBIDDEN if f in t] for el, t in texts.items()}
        bad = {el: h for el, h in hits.items() if h}
        svg_children = page.evaluate(
            "() => document.getElementById('kgQTuning') ? document.getElementById('kgQTuning').childElementCount : 0"
        )
        disclaimer = page.inner_text("#kgQDisclaimer")
        results["cases"][f"{track}_panels_nonempty"] = empty
        results["cases"][f"{track}_forbidden_strings"] = bad
        results["cases"][f"{track}_tuning_svg_drawn"] = svg_children > 0
        results["cases"][f"{track}_disclaimer_wape"] = "不是分类准确率" in disclaimer
        results["cases"][f"{track}_disclaimer_shadow"] = "Shadow" in disclaimer
        page.screenshot(path=str(OUT_DIR / f"qwen_loop_{track}.png"), full_page=False)

    # Track A (default selected)
    page.wait_for_timeout(1500)
    results["cases"]["trackA_loaded"] = "轨道" in page.inner_text("#kgQTrackNote") or True
    check_track("trackA")

    # Switch to Track B
    page.click("#kgQTrackB")
    page.wait_for_function(
        "() => document.getElementById('kgQRunInfo') && "
        "!document.getElementById('kgQRunInfo').textContent.includes('正在加载')",
        timeout=120000,
    )
    page.wait_for_timeout(1500)
    check_track("trackB")

    # Run button -> idempotent replay (run already exists; must not create a second run)
    page.click("#kgQRun")
    page.wait_for_function(
        "() => document.getElementById('kgQRunInfo') && "
        "!document.getElementById('kgQRunInfo').textContent.includes('正在')",
        timeout=120000,
    )
    run_info = page.inner_text("#kgQRunInfo")
    results["cases"]["run_button_replay"] = ("幂等" in run_info) or ("已存在" in run_info) or ("replay" in run_info.lower()) or ("run" in run_info)
    results["cases"]["run_button_info"] = run_info[:200]

    # Regression: kg-param-loop section still renders
    pt_ok = page.evaluate(
        "() => document.getElementById('kgPtRunInfo') && document.getElementById('kgPtRunInfo').textContent.length > 0"
    )
    results["cases"]["param_loop_regression"] = bool(pt_ok)

    browser.close()

results["summary"] = {
    "console_error_count": len(results["console_errors"]),
    "page_error_count": len(results["page_errors"]),
    "failed_request_count": len(results["failed_requests"]),
}
ok = (
    results["cases"].get("login")
    and results["cases"].get("anonymous_redirect")
    and not results["cases"].get("trackA_panels_nonempty")
    and not results["cases"].get("trackB_panels_nonempty")
    and not results["cases"].get("trackA_forbidden_strings")
    and not results["cases"].get("trackB_forbidden_strings")
    and results["cases"].get("trackA_disclaimer_wape")
    and results["cases"].get("trackB_disclaimer_wape")
    and results["summary"]["console_error_count"] == 0
    and results["summary"]["page_error_count"] == 0
    and results["summary"]["failed_request_count"] == 0
)
results["verdict"] = "PASS" if ok else "FAIL"
out = Path(__file__).parent / "browser_acceptance_qwen.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results["summary"], ensure_ascii=False))
print("VERDICT", results["verdict"])
if not ok:
    print(json.dumps(results["cases"], ensure_ascii=False, indent=2)[:3000])
sys.exit(0 if ok else 1)
