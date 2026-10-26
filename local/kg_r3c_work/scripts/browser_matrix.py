#!/usr/bin/env python3
"""Browser matrix for kg_r3c (runs locally against http://127.0.0.1:8130).
Password is fetched over SSH into memory only; never printed or persisted.
Screenshots -> local reports dir; summary -> 25_BROWSER_MATRIX.json (uploaded to server report dir).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = "http://127.0.0.1:8130"
SSH = ["ssh", "-o", "BatchMode=yes", "workstation-vm201-remote"]
OUT_DIR = Path(sys.argv[1])  # local output dir
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_credentials() -> tuple[str, str]:
    # local two-line file: user / password (never printed or persisted)
    lines = Path("secrets/dashboard_login.txt").read_text(encoding="utf-8").splitlines()
    return lines[0].strip(), lines[1].strip()


def run():
    from playwright.sync_api import sync_playwright
    username, password = get_credentials()
    results = {"base": BASE, "cases": {}, "screenshots": []}
    shot = lambda page, name: (page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=True),
                               results["screenshots"].append(f"{name}.png"))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)

        # --- case 1: anonymous access must be rejected ---
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        console_errors, page_errors = [], []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        resp = page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle", timeout=20000)
        results["cases"]["anonymous_redirect_or_401"] = {
            "status": resp.status if resp else None,
            "ended_on_login": "/login" in page.url or resp.status in (401, 403)}
        resp_api = page.request.get(BASE + "/api/v1/kg/recommendations")
        results["cases"]["anonymous_api_401"] = {"status": resp_api.status, "pass": resp_api.status == 401}
        shot(page, "01_anonymous")
        ctx.close()

        # --- login ---
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.goto(BASE + "/login", wait_until="networkidle", timeout=20000)
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=20000)
        results["cases"]["login"] = {"pass": "/login" not in page.url, "url_after": page.url.split("8130")[-1]}

        # --- KG page desktop ---
        page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text()
        results["cases"]["kg_page_desktop"] = {
            "has_r3c_section": page.locator("#kg-r3c").count() > 0,
            "utf8_no_mojibake": "\uFFFD" not in body and "????" not in body,
            "has_chinese": any("\u4e00" <= ch <= "\u9fff" for ch in body),
            "has_preexec_label": "PRE_EXECUTION_EVIDENCE_SCORE" in body,
            "has_deferred_label": "DEFERRED_NOT_EXECUTED" in body}
        shot(page, "02_kg_page_desktop")

        # --- drill: recommendations api drive (r3c endpoints) ---
        recs = page.request.get(BASE + "/api/v1/kg/r3c/recommendations?eligible_only=true&limit=200")
        recs_json = recs.json() if recs.status == 200 else {}
        rec_list = recs_json.get("items", recs_json if isinstance(recs_json, list) else [])
        results["cases"]["recommendations_api"] = {
            "status": recs.status, "count": len(rec_list),
            "score_type": recs_json.get("score_type"),
            "execution_status": recs_json.get("execution_status"),
            "all_eligible": all(r.get("eligibility_status") == "ELIGIBLE_RECOMMENDATION" for r in rec_list),
            "none_baseline": all(r.get("is_baseline") is False for r in rec_list)}
        if rec_list:
            rid = rec_list[0].get("recommendation_id")
            detail = page.request.get(BASE + f"/api/v1/kg/r3c/recommendations/{rid}")
            dj = detail.json() if detail.status == 200 else {}
            comps = dj.get("components", [])
            results["cases"]["recommendation_detail"] = {
                "status": detail.status,
                "top_level_keys": sorted(dj.keys())[:20],
                "six_dimensions": sorted({c.get("dimension") for c in comps if not c.get("is_penalty")}),
                "has_bundle": bool(dj.get("bundle")),
                "has_adapter": bool(dj.get("adapter_id") or dj.get("adapter")),
                "score_type": dj.get("score_type"),
                "execution_status": dj.get("execution_status")}
        # evidence drill
        papers = page.request.get(BASE + "/api/v1/kg/import-runs")
        results["cases"]["import_runs_api"] = {"status": papers.status}
        gvs = page.request.get(BASE + "/api/v1/kg/graph-versions")
        results["cases"]["graph_versions_api"] = {"status": gvs.status}
        if rec_list:
            pid = rec_list[0].get("profile_id")
            if pid:
                prof = page.request.get(BASE + f"/api/v1/kg/data-profiles/{pid}")
                results["cases"]["data_profile_api"] = {"status": prof.status}

        # --- expand drill-down UI if present ---
        if page.locator("#kg-r3c details, #kg-r3c .expandable, #kg-r3c button").count() > 0:
            page.locator("#kg-r3c details, #kg-r3c .expandable, #kg-r3c button").first.click()
            page.wait_for_timeout(500)
        shot(page, "03_kg_r3c_expanded")

        # --- old pages non-regression ---
        for path, name in [("/", "home"), ("/predictions", "predictions"),
                           ("/fasttrack-forecast", "fasttrack"), ("/kg", "kg_explorer")]:
            r = page.goto(BASE + path, wait_until="networkidle", timeout=20000)
            results["cases"][f"old_page_{name}"] = {
                "status": r.status if r else None,
                "no_server_error": "Internal Server Error" not in page.locator("body").inner_text()}

        # --- narrow viewport ---
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)
        results["cases"]["kg_page_narrow"] = {
            "has_r3c_section": page.locator("#kg-r3c").count() > 0,
            "no_horizontal_overflow": page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 40")}
        shot(page, "04_kg_page_narrow")

        results["console_error_count"] = len(console_errors)
        results["pageerror_count"] = len(page_errors)
        results["console_errors_head"] = console_errors[:5]
        ctx.close()
        browser.close()
    password = ""
    results["pass"] = (
        results["cases"].get("anonymous_api_401", {}).get("pass")
        and results["cases"].get("login", {}).get("pass")
        and results["cases"].get("kg_page_desktop", {}).get("has_r3c_section")
        and results["cases"].get("kg_page_desktop", {}).get("utf8_no_mojibake")
        and all(results["cases"].get(f"old_page_{n}", {}).get("status") == 200
                for n in ("home", "predictions", "fasttrack", "kg_explorer"))
        and results.get("pageerror_count", 1) == 0)
    return results


if __name__ == "__main__":
    res = run()
    (OUT_DIR / "25_BROWSER_MATRIX.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps({"pass": res["pass"], "screenshots": len(res["screenshots"]),
                      "cases": len(res["cases"])}, ensure_ascii=False))
