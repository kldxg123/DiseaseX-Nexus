from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    rc,out,err=helper.p111.p110r3.previous.p19.a100.run(client,"python3 - <<'PY'\nimport urllib.request\nprint(urllib.request.urlopen('https://stacks.cdc.gov/robots.txt',timeout=20).read().decode()[:2000])\nPY",timeout=60)
    print(out)
finally:
    client.close()
