from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    script = r'''
import json, pathlib
from playwright.sync_api import sync_playwright
out = pathlib.Path('/tmp/fasttrack_browser_console')
out.mkdir(parents=True, exist_ok=True)
console=[]
errors=[]
failed=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={"width":1440,"height":1100})
    page.on("console", lambda msg: console.append({"type":msg.type,"text":msg.text}))
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("requestfailed", lambda req: failed.append({"url":req.url,"failure":req.failure}))
    page.goto('http://127.0.0.1:8020/fasttrack-forecast', wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(5000)
    counts=page.evaluate("""() => ({
      summary: document.querySelector('#summary')?.innerText,
      optionCount: document.querySelectorAll('#locSelect option').length,
      chartChildren: document.querySelector('#chart')?.children.length,
      backtest: document.querySelector('#backtest')?.innerText?.slice(0,500),
      details: document.querySelector('#details')?.innerText?.slice(0,500),
    })""")
    page.screenshot(path=str(out/'probe.png'), full_page=True)
    browser.close()
print(json.dumps({"console":console,"errors":errors,"failed":failed,"counts":counts}, ensure_ascii=False, sort_keys=True))
'''
    tmp = Path("/tmp/fasttrack_browser_console_probe.py")
    tmp.write_text(script, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(tmp)], text=True, capture_output=True, timeout=90)
    print(proc.stdout[:10000])
    if proc.returncode:
        print(proc.stderr[:4000])
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
