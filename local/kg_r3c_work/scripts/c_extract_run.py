#!/usr/bin/env python3
"""Phase C runner: evidence extraction over imported pages + graph version publish."""
import json, os, sys
sys.path.insert(0, "USER_HOME/projects/openclaw/src")
from epidemic_core.kg_r3c.dbutil import connect
from epidemic_core.kg_r3c.evidence import run_extraction, build_evidence_index
from epidemic_core.kg_r3c.graph_version import publish_graph_version

REPORT_DIR = sys.argv[1]
IMPORT_RUN_ID = "bef74d0c-f60e-58d8-af98-d646a6401958"
conn = connect()
stats = run_extraction(conn, IMPORT_RUN_ID)
conn.commit()
print("extraction:", json.dumps(stats, indent=1, default=str))

gv = publish_graph_version(conn, IMPORT_RUN_ID)
conn.commit()
print("graph_version:", json.dumps(gv, indent=1))

idx = build_evidence_index(conn, IMPORT_RUN_ID)
print("evidence_index:", json.dumps({k: {kk: len(vv) for kk, vv in v.items()}
                                     for k, v in idx.items()}, indent=1))
conn.close()
