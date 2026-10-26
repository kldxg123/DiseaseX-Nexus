# ruff: noqa: E501  # long lines hold verbatim paper excerpts / hashes / Chinese UI strings
"""Browser acceptance for the patched KG page on the LOCAL staging app.

Runs against the COMPLETE page (all sections, production structure). Checks:
login flow, formal RSOS benchmark block renders with expected content, legacy
preprint block retained and correctly labeled, all section status elements
settle into explicit states (never stuck on "正在读取/正在加载/加载中…" fake
loading), canonical benchmark route equals the alias route, console/pageerror/
first-party failed-request counts are zero. Any empty required DOM fails loudly.
Saves sanitized PNG screenshots under kg_paper_benchmark_work/screenshots/ and
the JSON report at kg_paper_benchmark_work/out_browser_acceptance.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8199"
WORK = Path("F:/作业/aibola/kg_paper_benchmark_work")
OUT_DIR = WORK / "screenshots"
OUT_DIR.mkdir(exist_ok=True)
OUT_JSON = WORK / "out_browser_acceptance.json"

# Status elements that must settle into an explicit state (explicit texts such
# as 加载失败/暂无/尚未运行/操作提示 are allowed; fake perpetual loading is not).
STATUS_IDS = [
    "kgStatus", "kgR3cStatus", "kgDlStatus", "kgPtRunInfo", "kgQRunInfo",
    "kgBenchmarkRsosStatus", "kgBenchmarkEStatus",
]
LOADING_PATTERNS = ["正在读取", "正在加载", "加载中…", "加载中..."]
# Sections that must exist on the full page.
SECTION_IDS = ["kg-r3c", "kg-dual-loop", "kg-param-loop", "kg-qwen-loop",
               "kg-paper-benchmark-rsos", "kg-paper-benchmark-e"]
# Required non-empty DOM nodes (RSOS seven + legacy block key nodes).
DOM_IDS = [
    "kgBenchmarkRsosStatus", "kgBenchmarkRsosPaper", "kgBenchmarkRsosData",
    "kgBenchmarkRsosComparators", "kgBenchmarkRsosProject", "kgBenchmarkRsosHorizon",
    "kgBenchmarkRsosAudit",
    "kgBenchmarkEData", "kgBenchmarkEMetrics", "kgBenchmarkEComparators",
]

results: dict = {"cases": {}, "console_errors": [], "page_errors": [], "failed_requests": []}


def attach_listeners(page, tag):
    page.on("console", lambda m: results["console_errors"].append({"page": tag, "text": m.text[:300]}) if m.type == "error" else None)
    page.on("pageerror", lambda e: results["page_errors"].append({"page": tag, "text": str(e)[:300]}))

    def on_resp(resp):
        if resp.status >= 400 and resp.url.startswith(BASE):
            results["failed_requests"].append({"page": tag, "url": resp.url[len(BASE):][:120], "status": resp.status})

    page.on("response", on_resp)


with sync_playwright() as p:
    browser = p.chromium.launch()

    # anonymous API access must be rejected with 401 (no session cookie)
    anon = p.request.new_context(base_url=BASE)
    anon_resp = anon.get("/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-mpox-2024")
    results["cases"]["anonymous_api_401"] = anon_resp.status == 401
    anon.dispose()

    page = browser.new_page(viewport={"width": 1440, "height": 1600})
    attach_listeners(page, "kg")

    page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="domcontentloaded")
    results["cases"]["anonymous_redirect"] = "/login" in page.url

    page.goto(BASE + "/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', "stage")
    page.fill('input[name="password"]', "stage")
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")
    results["cases"]["login"] = "/login" not in page.url

    page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle")
    page.wait_for_timeout(2500)

    html = page.content()
    rsos = page.locator("#kg-paper-benchmark-rsos")
    legacy = page.locator("#kg-paper-benchmark-e")
    results["cases"]["rsos_block_present"] = rsos.count() == 1
    results["cases"]["legacy_block_present"] = legacy.count() == 1
    results["cases"]["rsos_shows_formal_paper"] = "Bleichrodt" in html and "10.1098/rsos.240248" in html
    results["cases"]["rsos_shows_jcr"] = "JCR" in html and ("Q1" in html or "Q2" in html)
    results["cases"]["rsos_shows_transfer_label"] = "METHOD_TRANSFER_REPLICATION" in html
    results["cases"]["rsos_shows_paired_note"] = "不是分类准确率" in html
    results["cases"]["legacy_labeled_preprint"] = "LEGACY_PREPRINT_NOT_PRIMARY_BENCHMARK" in html
    results["cases"]["rsos_no_error_state"] = "加载失败" not in (rsos.inner_text() if rsos.count() else "加载失败")

    # Content-level check: API/JSON fields must actually reach the DOM as numbers
    # (guards against key-schema drift between benchmark JSON and the JS renderer).
    ref = json.loads((WORK / "page_patch/src/epidemic_core/forecast_model_kg/reference/mpox_rsos2024_paper_benchmark.v1.json").read_text(encoding="utf-8"))
    expected_agree = f"{ref['metrics']['by_model_overall']['paper_arima']['test']['agreement_pct']:.2f}%"
    expected_wape = f"{ref['metrics']['by_model_overall']['paper_arima']['test']['wape']*100:.2f}%"
    comp_text = page.locator("#kgBenchmarkRsosComparators").inner_text()
    results["cases"]["rsos_comparators_show_real_numbers"] = expected_agree in comp_text and expected_wape in comp_text
    horizon_text = page.locator("#kgBenchmarkRsosHorizon").inner_text()
    expected_h1 = f"{ref['metrics']['by_horizon_wape']['paper_arima']['1']*100:.2f}%"
    results["cases"]["rsos_horizon_show_real_numbers"] = expected_h1 in horizon_text
    results["cases"]["rsos_paper_shows_both_diseases"] = "mpox" in page.locator("#kgBenchmarkRsosPaper").inner_text() and "dengue" in page.locator("#kgBenchmarkRsosPaper").inner_text()
    results["cases"]["legacy_no_error_state"] = "加载失败" not in (legacy.inner_text() if legacy.count() else "加载失败")

    # all sections of the full page must exist
    for sec_id in SECTION_IDS:
        results["cases"][f"section_{sec_id}"] = page.locator("#" + sec_id).count() == 1

    # status elements must settle into explicit states, never fake loading
    for sid in STATUS_IDS:
        el = page.locator("#" + sid)
        txt = el.inner_text().strip() if el.count() else ""
        settled = el.count() == 1 and len(txt) > 0 and not any(pat in txt for pat in LOADING_PATTERNS)
        results["cases"][f"status_settled_{sid}"] = settled
        if not settled:
            print(f"FAIL: status element #{sid} stuck or empty: {txt[:80]!r}", file=sys.stderr)

    # key DOM ids populated (fail loudly on empty)
    for dom_id in DOM_IDS:
        el = page.locator("#" + dom_id)
        ok = el.count() == 1 and len(el.inner_text().strip()) > 10
        results["cases"][f"dom_{dom_id}"] = ok
        if not ok:
            print(f"FAIL: DOM node #{dom_id} empty or missing", file=sys.stderr)

    # canonical route and alias route both 200 with identical DOI
    alias_resp = page.request.get(BASE + "/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-mpox-2024")
    canon_resp = page.request.get(BASE + "/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-240248-method-transfer-sgp-dengue")
    alias_doi = alias_resp.json().get("paper", {}).get("doi") if alias_resp.ok else None
    canon_doi = canon_resp.json().get("paper", {}).get("doi") if canon_resp.ok else None
    results["cases"]["canonical_route_200"] = canon_resp.status == 200
    results["cases"]["alias_route_200"] = alias_resp.status == 200
    results["cases"]["canonical_alias_doi_consistent"] = (
        alias_doi is not None and alias_doi == canon_doi == "10.1098/rsos.240248"
    )
    results["doi"] = {"alias": alias_doi, "canonical": canon_doi}

    page.screenshot(path=str(OUT_DIR / "kg_benchmark_blocks.png"), full_page=True)
    page.locator("#kg-r3c").screenshot(path=str(OUT_DIR / "kg_stub_sections_r3c.png"))
    page.locator("#kg-param-loop").screenshot(path=str(OUT_DIR / "kg_stub_sections_param_loop.png"))
    page.locator("#kg-qwen-loop").screenshot(path=str(OUT_DIR / "kg_stub_sections_qwen_loop.png"))
    browser.close()

results["cases"]["zero_console_errors"] = len(results["console_errors"]) == 0
results["cases"]["zero_page_errors"] = len(results["page_errors"]) == 0
results["cases"]["zero_failed_requests"] = len(results["failed_requests"]) == 0
results["summary"] = {
    "all_cases_pass": all(results["cases"].values()),
    "console_errors": len(results["console_errors"]),
    "page_errors": len(results["page_errors"]),
    "failed_requests": len(results["failed_requests"]),
}
OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results["summary"], ensure_ascii=False))
print(json.dumps(results["cases"], ensure_ascii=False, indent=2))
sys.exit(0 if results["summary"]["all_cases_pass"] else 1)
