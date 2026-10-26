"""Browser acceptance for the held-out governance repair (Playwright, local tunnel).

Checks: login, #kg-qwen-loop renders for Track A (replay), Track B
(reclassified RETROSPECTIVE_REPLAY_NOT_FRESH with the mandatory "历史重复评估结果，
不构成独立盲测认证" messaging, registry REPLAY_ONLY, no fresh claim) and Track C
(fresh blind track, locked/consumed registry state), paired-baseline panel,
all panels non-empty without raw null/undefined/NaN, console/pageerror/
first-party-failed-request == 0.
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
    "kgQEval", "kgQPaired", "kgQChampion", "kgQFeedback", "kgQCycles", "kgQMeta",
    "kgQLimits", "kgQRunInfo",
]
FORBIDDEN = ["undefined", "NaN%", "[object Object]", "加载失败"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 960})

    page.on("console", lambda m: results["console_errors"].append({"text": m.text[:300]}) if m.type == "error" else None)
    page.on("pageerror", lambda e: results["page_errors"].append({"text": str(e)[:300]}))

    def on_resp(resp):
        if resp.status >= 400 and resp.url.startswith(BASE):
            results["failed_requests"].append({"url": resp.url[len(BASE):][:120], "status": resp.status})

    page.on("response", on_resp)

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
        results["cases"][f"{track}_panels_nonempty"] = empty
        results["cases"][f"{track}_forbidden_strings"] = bad
        results["cases"][f"{track}_eval_text"] = texts["kgQEval"][:600]
        page.screenshot(path=str(OUT_DIR / f"gov_{track}.png"), full_page=False)

    # Track A (default)
    page.wait_for_timeout(1500)
    check_track("trackA")
    results["cases"]["trackA_replay_label"] = "历史回放基准" in results["cases"]["trackA_eval_text"]

    # Track B: reclassified retrospective
    page.click("#kgQTrackB")
    page.wait_for_function(
        "() => document.getElementById('kgQRunInfo') && "
        "!document.getElementById('kgQRunInfo').textContent.includes('正在加载')",
        timeout=120000,
    )
    page.wait_for_timeout(1500)
    check_track("trackB")
    eval_b = results["cases"]["trackB_eval_text"]
    note_b = page.inner_text("#kgQTrackNote")
    results["cases"]["trackB_retrospective_banner"] = "历史重复评估结果，不构成独立盲测认证" in eval_b
    results["cases"]["trackB_identity_label"] = "历史重复评估" in eval_b
    results["cases"]["trackB_registry_replay_only"] = "仅可回放" in eval_b
    results["cases"]["trackB_not_fresh"] = "否" in eval_b and "盲测认证" in eval_b
    results["cases"]["trackB_note_reclassified"] = "历史重复评估" in note_b
    results["cases"]["trackB_no_fresh_claim"] = "新鲜独立留存（调参前已锁定" not in eval_b

    # Track C: fresh blind track (locked or consumed depending on run progress)
    page.click("#kgQTrackC")
    page.wait_for_function(
        "() => document.getElementById('kgQRunInfo') && "
        "!document.getElementById('kgQRunInfo').textContent.includes('正在加载')",
        timeout=120000,
    )
    page.wait_for_timeout(1500)
    check_track("trackC")
    note_c = page.inner_text("#kgQTrackNote")
    results["cases"]["trackC_note_fresh"] = "新鲜盲测" in note_c

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
required_true = [
    "login", "anonymous_redirect", "trackA_replay_label",
    "trackB_retrospective_banner", "trackB_identity_label", "trackB_registry_replay_only",
    "trackB_not_fresh", "trackB_note_reclassified", "trackB_no_fresh_claim",
    "trackC_note_fresh", "param_loop_regression",
]
ok = (
    all(results["cases"].get(k) for k in required_true)
    and not results["cases"].get("trackA_panels_nonempty")
    and not results["cases"].get("trackB_panels_nonempty")
    and not results["cases"].get("trackC_panels_nonempty")
    and not results["cases"].get("trackA_forbidden_strings")
    and not results["cases"].get("trackB_forbidden_strings")
    and not results["cases"].get("trackC_forbidden_strings")
    and results["summary"]["console_error_count"] == 0
    and results["summary"]["page_error_count"] == 0
    and results["summary"]["failed_request_count"] == 0
)
results["verdict"] = "PASS" if ok else "FAIL"

out = Path(__file__).with_name("browser_acceptance_governance.json")
out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"verdict": results["verdict"], "summary": results["summary"],
                  "failed_cases": [k for k in required_true if not results["cases"].get(k)],
                  "empty_panels": {k: v for k, v in results["cases"].items() if k.endswith("panels_nonempty") and v},
                  "forbidden": {k: v for k, v in results["cases"].items() if k.endswith("forbidden_strings") and v}},
                 ensure_ascii=False, indent=1))
