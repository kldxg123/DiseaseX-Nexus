"""Browser acceptance for the KG dual-loop section (Playwright, local tunnel).

Checks: login, Austria full loop, Peru full loop, dataset switching without
context bleed, refresh reproducibility, analyst feedback round-trip, empty
console/pageerror/first-party-failure counts, and regression on /,
/predictions, /fasttrack-forecast. Saves sanitized PNG screenshots only.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8130"
OUT_DIR = Path(__file__).parent / "screenshots"
OUT_DIR.mkdir(exist_ok=True)

creds = (Path(__file__).parent.parent / "secrets" / "dashboard_login.txt").read_text(encoding="utf-8").splitlines()
USER, PW = creds[0].strip(), creds[1].strip()

results = {"cases": {}, "console_errors": [], "page_errors": [], "failed_requests": []}


def attach_listeners(page, tag):
    page.on("console", lambda m: results["console_errors"].append({"page": tag, "text": m.text[:300]}) if m.type == "error" else None)
    page.on("pageerror", lambda e: results["page_errors"].append({"page": tag, "text": str(e)[:300]}))
    def on_resp(resp):
        if resp.status >= 400 and resp.url.startswith(BASE):
            results["failed_requests"].append({"page": tag, "url": resp.url[len(BASE):][:120], "status": resp.status})
    page.on("response", on_resp)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    attach_listeners(page, "kg")

    # anonymous page redirects to login
    resp = page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="domcontentloaded")
    results["cases"]["anonymous_redirect"] = "/login" in page.url

    # login
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    results["cases"]["login"] = "/login" not in page.url

    def run_dual_loop(ds_value, shot):
        page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle")
        page.select_option("#kgDlDataset", ds_value)
        page.click("#kgDlRun")
        page.wait_for_function(
            "() => document.getElementById('kgDlStatus').textContent.includes('已加载')",
            timeout=60000)
        texts = {}
        for el in ("kgDlProfile", "kgDlRec", "kgDlRunInfo", "kgDlBacktest", "kgDlForecast", "kgDlFeedback", "kgDlLimits"):
            texts[el] = page.inner_text(f"#{el}")
        ok = all(t and "加载失败" not in t and "null" != t.strip().lower() for t in texts.values())
        nonempty = all(len(t.strip()) > 10 for t in texts.values())
        chart_children = page.eval_on_selector("#kgDlChart", "e => e.childElementCount")
        page.screenshot(path=str(OUT_DIR / shot), full_page=False)
        return {"render_ok": ok and nonempty, "chart_elements": chart_children,
                "status": page.inner_text("#kgDlStatus")[:120],
                "profile_excerpt": texts["kgDlProfile"][:150],
                "backtest_excerpt": texts["kgDlBacktest"][:200],
                "limits_excerpt": texts["kgDlLimits"][:150]}

    results["cases"]["austria_loop"] = run_dual_loop("AUT", "dual_loop_austria.png")
    results["cases"]["peru_loop"] = run_dual_loop("PER", "dual_loop_peru.png")

    # explicit dataset switch: Peru view must not show Austria context
    peru_status = results["cases"]["peru_loop"]["status"]
    results["cases"]["switch_no_context_bleed"] = "秘鲁" in peru_status and "奥地利" not in peru_status

    # refresh reproducibility: same run id after reload
    page.reload(wait_until="networkidle")
    page.select_option("#kgDlDataset", "PER")
    page.click("#kgDlRun")
    page.wait_for_function("() => document.getElementById('kgDlStatus').textContent.includes('已加载')", timeout=60000)
    status_after = page.inner_text("#kgDlStatus")
    results["cases"]["refresh_reproducible"] = results["cases"]["peru_loop"]["status"][-20:] in status_after or "run_id" in status_after

    # analyst feedback via UI
    page.fill('#kgDlFeedbackForm input[name="actor"]', "browser-acceptance")
    page.select_option('#kgDlFeedbackForm select[name="decision_code"]', "EXPLANATION_CLEAR")
    page.fill('#kgDlFeedbackForm input[name="comment"]', "browser e2e check")
    page.click('#kgDlFeedbackForm button[type="submit"]')
    page.wait_for_function("() => document.getElementById('kgDlFeedbackMsg').textContent.includes('已记录')", timeout=30000)
    page.wait_for_function("() => document.getElementById('kgDlStatus').textContent.includes('已加载')", timeout=60000)
    fb_text = page.inner_text("#kgDlFeedback")
    results["cases"]["analyst_feedback_ui"] = "browser-acceptance" in fb_text
    page.screenshot(path=str(OUT_DIR / "dual_loop_feedback.png"), full_page=False)

    # regression pages
    for path, name in [("/", "root"), ("/predictions", "predictions"), ("/fasttrack-forecast", "fasttrack")]:
        before_c = len(results["console_errors"]); before_p = len(results["page_errors"]); before_f = len(results["failed_requests"])
        r = page.goto(BASE + path, wait_until="networkidle")
        results["cases"][f"regression_{name}"] = {"status": r.status,
                                                  "no_new_errors": (len(results["console_errors"]) == before_c
                                                                    and len(results["page_errors"]) == before_p
                                                                    and len(results["failed_requests"]) == before_f)}
    browser.close()

results["console_error_count"] = len(results["console_errors"])
results["pageerror_count"] = len(results["page_errors"])
results["first_party_failed_request_count"] = len(results["failed_requests"])
(Path(__file__).parent / "browser_acceptance.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in results.items() if k != "cases"}, indent=2, ensure_ascii=False))
print(json.dumps(results["cases"], indent=2, ensure_ascii=False, default=str)[:2500])
ok = (results["console_error_count"] == 0 and results["pageerror_count"] == 0
      and results["first_party_failed_request_count"] == 0
      and all(c.get("render_ok", True) if isinstance(c, dict) else c for c in results["cases"].values()))
print("BROWSER_ACCEPTANCE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
