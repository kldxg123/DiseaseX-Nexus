from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
OUTDIR = Path(REPORT) / "artifacts" / "cdc_stacks_legionellosis"
OUTDIR.mkdir(parents=True, exist_ok=True)
MANIFEST = Path(REPORT) / "US_LEGIONELLOSIS_STACKS_DISCOVERY.json"

BASE = "https://stacks.cdc.gov/cbrowse?maxResults=100&parentId=cdc%3A49375&pid=cdc%3A49375&start={start}"
RE_ITEM = re.compile(r"Legionellosis \\(Week (\\d+)\\).*?week ending ([A-Za-z]+ \\d{1,2}, \\d{4}).*?URL : https://stacks\\.cdc\\.gov/view/cdc/(\\d+)", re.S)
RE_SIMPLE = re.compile(r"https://stacks\\.cdc\\.gov/view/cdc/(\\d+)")


def fetch(url: str) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-stacks/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, resp.read(2_000_000), dict(resp.headers.items())


def main() -> None:
    found: dict[str, dict] = {}
    pages = []
    empty_runs = 0
    for start in range(0, 12000, 100):
        url = BASE.format(start=start)
        try:
            status, body, headers = fetch(url)
        except Exception as exc:
            pages.append({"start": start, "status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)[:200]})
            empty_runs += 1
            if empty_runs >= 5:
                break
            continue
        text = body.decode("utf-8", "replace")
        matches = list(RE_ITEM.finditer(text))
        # Fallback: split around Legionellosis snippets if crawler text order differs.
        if not matches and "Legionellosis" in text:
            for chunk in text.split("Legionellosis")[1:]:
                chunk = "Legionellosis" + chunk[:3000]
                ids = RE_SIMPLE.findall(chunk)
                wk = re.search(r"Week (\\d+)", chunk)
                date = re.search(r"week ending ([A-Za-z]+ \\d{1,2}, \\d{4})", chunk)
                if ids and wk and date:
                    cid = ids[0]
                    found[cid] = {
                        "cdc_id": cid,
                        "week": int(wk.group(1)),
                        "week_ending_text": date.group(1),
                        "view_url": f"https://stacks.cdc.gov/view/cdc/{cid}",
                        "source": "cbrowse_snippet",
                    }
        for m in matches:
            week, date, cid = m.groups()
            found[cid] = {
                "cdc_id": cid,
                "week": int(week),
                "week_ending_text": date,
                "view_url": f"https://stacks.cdc.gov/view/cdc/{cid}",
                "source": "cbrowse_regex",
            }
        pages.append({
            "start": start,
            "status": status,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "legionellosis_hits_total_so_far": len(found),
            "contains_legionellosis": "Legionellosis" in text,
        })
        if len(found) >= 156:
            break
        if "Legionellosis" not in text:
            empty_runs += 1
        else:
            empty_runs = 0
        if empty_runs >= 20 and start > 2000:
            break
        time.sleep(0.2)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "CDC Stacks official NNDSS collection",
        "soda_status": "BLOCKED_403_ON_VM201_RECORDED",
        "items": sorted(found.values(), key=lambda x: (x.get("week_ending_text", ""), x["cdc_id"])),
        "item_count": len(found),
        "pages": pages,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"item_count": len(found), "manifest": str(MANIFEST)}, sort_keys=True))


if __name__ == "__main__":
    main()
