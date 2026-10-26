#!/usr/bin/env python3
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

lines = Path("secrets/dashboard_login.txt").read_text(encoding="utf-8").splitlines()
BASE = "http://127.0.0.1:8130"
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(BASE + "/login", wait_until="networkidle", timeout=20000)
    page.fill('input[name="username"]', lines[0].strip())
    page.fill('input[name="password"]', lines[1].strip())
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle", timeout=20000)
    page.goto(BASE + "/forecast-model-knowledge-graph", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    hits = page.evaluate("""() => {
      const out = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walker.nextNode())) {
        const t = n.nodeValue;
        if (t.includes('????') || t.includes('\\uFFFD')) {
          const el = n.parentElement;
          out.push({tag: el.tagName, id: el.id, cls: el.className,
                    in_r3c: !!el.closest('#kg-r3c'),
                    text: t.trim().slice(0, 120)});
        }
      }
      return out.slice(0, 20);
    }""")
    import json
    print(json.dumps(hits, ensure_ascii=False, indent=1))
    browser.close()
