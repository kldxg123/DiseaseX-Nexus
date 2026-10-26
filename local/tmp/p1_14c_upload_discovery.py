from pathlib import Path
import sys, hashlib, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import a100_p1_12 as helper
local=Path('remote_edit/p1_12_current/src/epidemic_core/p1_12/discovery.py')
client,_=helper.p111.p110r3.previous.p19.a100.connect()
try:
    _,project=helper.p111.p110r3.previous.p19.a100.paths(client)
    remote=f'{project}/src/epidemic_core/p1_12/discovery.py'
    with client.open_sftp() as sftp:
        sftp.put(str(local), remote)
    print(json.dumps({'uploaded':'src/epidemic_core/p1_12/discovery.py','sha256':hashlib.sha256(local.read_bytes()).hexdigest()}))
finally:
    client.close()
