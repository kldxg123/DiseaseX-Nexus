#!/usr/bin/env python3
"""Phase D runner: snapshots/profiles/tasks + 15_DATA_PROFILES.csv."""
import csv, json, os, sys
sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.dbutil import connect
from epidemic_core.kg_r3c.profiles import snapshot_and_profile

REPORT_DIR = sys.argv[1]
conn = connect()
results = snapshot_and_profile(conn)
conn.commit()

cols = ["profile_id", "country", "disease", "indicator", "frequency", "n_obs",
        "n_nonzero", "zero_ratio", "mean", "variance", "overdispersion", "min", "max",
        "negative_count", "semantics", "is_ultra_short", "trend", "seasonality",
        "outlier_count", "first_date", "last_date", "profile_sha256"]
with open(os.path.join(REPORT_DIR, "15_DATA_PROFILES.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for r in results:
        p = r["profile"]
        w.writerow([r["profile_id"], p["country"], p["disease"], p["indicator"], p["frequency"],
                    p["n_obs"], p["n_nonzero"], round(p["zero_ratio"], 4) if p["zero_ratio"] is not None else "",
                    round(p["mean"], 4), round(p["variance"], 4),
                    round(p["overdispersion"], 4) if p["overdispersion"] else "",
                    p["min"], p["max"], p["negative_count"], p["semantics"], p["is_ultra_short"],
                    p["trend"]["trend"], p["seasonality"]["seasonality"],
                    p["outliers_changepoints"]["outlier_count"],
                    p["first_date"], p["last_date"], r["profile_sha256"][:16]])
print(json.dumps({"live_profile_count": len(results),
                  "semantics": {s: sum(1 for r in results if r["profile"]["semantics"] == s)
                                for s in sorted({r["profile"]["semantics"] for r in results})},
                  "ultra_short": sum(1 for r in results if r["profile"]["is_ultra_short"])}, indent=1))
conn.close()
