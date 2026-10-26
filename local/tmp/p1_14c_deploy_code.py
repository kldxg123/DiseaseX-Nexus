from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
ROOT=Path('remote_edit/p1_12_current')
FILES=[
 'src/epidemic_core/ebola/fetcher.py',
 'src/epidemic_core/p1_12/discovery.py',
 'migrations/versions/0027_p1_14c_v2_preview_path_grants.py',
 'tests/unit/test_p1_14c_cdc_stacks_xlsx.py',
 'tests/unit/test_p1_14c_v2_preview_path_grants.py',
]
def sha(p:Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    before={}
    with client.open_sftp() as sftp:
        for rel in FILES:
            local=ROOT/rel
            remote=f'{project}/{rel}'
            try:
                with sftp.open(remote,'rb') as f:
                    before[rel]=hashlib.sha256(f.read()).hexdigest()
            except OSError:
                before[rel]=None
        for rel in FILES:
            local=ROOT/rel
            remote=f'{project}/{rel}'
            parent=remote.rsplit('/',1)[0]
            helper.p111.p110r3.previous.p19.a100.checked(client, f'mkdir -p {helper.q(parent)}')
            sftp.put(str(local), remote)
    after={rel: sha(ROOT/rel) for rel in FILES}
    print(json.dumps({'uploaded':FILES,'before':before,'after':after}, indent=2))
finally:
    client.close()
