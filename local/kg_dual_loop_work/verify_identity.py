"""Identity gate verification: GraphVersions, contracts, bundles, snapshots."""
import hashlib
import json

from db import connect

conn = connect()
out = {}

def cols(table, schema="epidemic_kg"):
    return [d.name for d in conn.execute(f"select * from {schema}.{table} limit 0").description]

def row(table, where, params, schema="epidemic_kg"):
    c = cols(table, schema)
    r = conn.execute(f"select * from {schema}.{table} where {where}", params).fetchone()
    return dict(zip(c, r)) if r else None

# Graph versions detail
gv_cols = cols("kg_graph_versions")
gvs = conn.execute("select * from epidemic_kg.kg_graph_versions order by 1").fetchall()
out["graph_versions"] = [dict(zip(gv_cols, g)) for g in gvs]

# recommendation run + graph version binding
rr = conn.execute("select * from epidemic_kg.kg_recommendation_runs").fetchall()
rr_cols = cols("kg_recommendation_runs")
out["recommendation_runs"] = [dict(zip(rr_cols, r)) for r in rr]

# contracts full
for label, cid in [("A", "c24475b7-84ac-51ea-8f83-35542a1e789a"),
                   ("B", "49915645-4ddf-53fd-b2c5-4dff56faac5e")]:
    r = row("kg_execution_contracts", "contract_id=%s", (cid,))
    out[f"contract_{label}"] = r
    if r:
        canon = json.dumps(r["contract"], sort_keys=True, separators=(",", ":"))
        out[f"contract_{label}_sha_recomputed"] = hashlib.sha256(canon.encode()).hexdigest()
        out[f"contract_{label}_sha_stored"] = r["contract_sha256"]

# snapshots
for label, pid in [("A", "96e767d3-7dbd-56d8-949d-dc407966bfd2"),
                   ("B", "a324e51a-45a6-574e-a823-df7ac4465efa")]:
    p = row("kg_data_profiles", "profile_id=%s", (pid,))
    out[f"profile_{label}"] = p
    if p:
        s = row("kg_data_snapshots", "snapshot_id=%s", (str(p["snapshot_id"]),))
        out[f"snapshot_{label}"] = s

# bundles
for label, bid in [("A", "65517287-63f2-5cc8-bde1-814c8314116c"),
                   ("B", "4f7b4407-824c-5dc7-b8a0-bbbaafbf4a93")]:
    out[f"bundle_{label}"] = row("kg_parameter_bundles", "bundle_id=%s", (bid,))

# recommendation components count for A/B recs
for label, rid in [("A", "c219de41-ea2e-58d9-86cd-339b03213401"),
                   ("B", "ca2f64b8-2926-5ca1-9d9e-4585980849c2")]:
    n = conn.execute(
        "select count(*) from epidemic_kg.kg_recommendation_components where recommendation_id=%s",
        (rid,)).fetchone()[0]
    out[f"rec_components_{label}_count"] = n

# score policy
sp_cols = cols("kg_score_policies")
sp = conn.execute("select * from epidemic_kg.kg_score_policies").fetchall()
out["score_policies"] = [dict(zip(sp_cols, r)) for r in sp]

with open("identity_gate.json", "w") as f:
    json.dump(out, f, indent=2, default=str)

print("graph versions:")
for g in out["graph_versions"]:
    print(json.dumps(g, default=str))
print("recommendation runs:")
for r in out["recommendation_runs"]:
    print(json.dumps(r, default=str)[:800])
for label in ("A", "B"):
    c = out[f"contract_{label}"]
    print(f"contract_{label} status:", c["status"], "sha match:",
          out[f"contract_{label}_sha_recomputed"] == out[f"contract_{label}_sha_stored"])
    print(f"snapshot_{label} sha:", out[f"snapshot_{label}"]["snapshot_sha256"])
    print(f"bundle_{label}:", json.dumps(out[f"bundle_{label}"], default=str)[:400])
    print(f"rec_components_{label}:", out[f"rec_components_{label}_count"])
print("score policy:", json.dumps(out["score_policies"], default=str)[:300])
