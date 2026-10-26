from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
client, _gate = helper.p111.p110r3.previous.p19.a100.connect()
try:
    outdir = Path('reports/p1_14c_v2_preview_path')
    outdir.mkdir(parents=True, exist_ok=True)
    with client.open_sftp() as sftp:
        for name in ('diagnosis.json','diagnosis.md'):
            sftp.get('/tmp/p1_14c_v2_preview_path/' + name, str(outdir/name))
    print(outdir.resolve())
finally:
    client.close()
