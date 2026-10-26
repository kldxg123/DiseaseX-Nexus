from __future__ import annotations
from pathlib import Path
import re
s = Path("cdc_258228_page.html").read_text(encoding="utf-8", errors="ignore")
print(s[:500])
print("Legionellosis", "Legionellosis" in s)
print("ids", re.findall(r"cdc/[0-9]+", s)[:20])
print("hrefs", re.findall(r'href="([^"]+)"', s)[:30])
for pat in ["canonical", "api", "download", "DS2", "parent", "Week"]:
    print(pat, s.find(pat))
