from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client, _gate = helper.p111.p110r3.previous.p19.a100.connect()
try:
    data_root, project = helper.p111.p110r3.previous.p19.a100.paths(client)
    script = r'''
set -euo pipefail
cd "$PROJECT"
for f in .secrets/p1_1.env .secrets/postgres.env .secrets/preview.env .secrets/trusted_ingest.env ../epidemic-source-review-runtime/secrets/review.env ../epidemic-dashboard-runtime/secrets/dashboard.env; do
  echo "FILE:${f#../}"
  if [ -f "$f" ]; then sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$f" | sort; else echo MISSING; fi
done
'''
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, f"PROJECT={helper.q(project)} bash -lc {helper.q(script)}", timeout=120)
    print(out)
finally:
    client.close()
