from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _, project=helper.p111.p110r3.previous.p19.a100.paths(client)
    for rel in ['scripts/database/_env.sh','scripts/source_discovery_worker/_common.sh','scripts/daily_update/_common.sh','scripts/query_api/query_api_env.sh','scripts/source_review/_common.sh']:
        rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, f"cd {helper.q(project)}; if [ -f {helper.q(rel)} ]; then echo FILE:{rel}; sed -n '1,180p' {helper.q(rel)} | sed -E 's#(postgresql[^ ]+|PASSWORD=.*|DATABASE_URL=.*)#<redacted>#g'; fi", timeout=120)
        print(out)
finally:
    client.close()
