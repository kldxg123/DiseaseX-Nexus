from pathlib import Path

patterns = ["EPIDEMIC_ROLE_PASS", "PG_SUPERPASS", "55432", "epidemic_openclaw"]
roots = [Path("src"), Path("scripts"), Path("tests"), Path("config")]
hits = []
for root in roots:
    if not root.exists():
        continue
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(x in line for x in patterns):
                hits.append((str(p), i, line[:180]))
                break
for h in hits[:100]:
    print(f"{h[0]}:{h[1]}:{h[2]}")
