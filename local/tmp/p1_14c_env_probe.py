from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client, _gate = helper.p111.p110r3.previous.p19.a100.connect()
try:
    data_root, project = helper.p111.p110r3.previous.p19.a100.paths(client)
    cmd = f"bash -lc {helper.q('set -e; cd ' + helper.q(project) + '; find .. -maxdepth 3 -type f \( -name "*.env" -o -name "*env.sh" -o -name "*.conf" \) 2>/dev/null | sed "s#^../##" | sort | head -200')}"
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client, cmd, timeout=120)
    print(out)
finally:
    client.close()
