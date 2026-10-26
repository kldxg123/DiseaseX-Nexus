"""Pinned-host experiment access; never emit connection credentials."""
from pathlib import Path
import sys,json,base64,re
import paramiko

ROOT=Path(__file__).resolve().parents[1]

def connect(login=None):
    if not login:raise RuntimeError('Explicit approved login alias required')
    known=ROOT/'known_hosts_vm201_openclaw'
    pins=paramiko.HostKeys(str(known))
    class PinnedKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self,client,hostname,key):
            if not any(key==pinned for keys in pins.values() for pinned in keys.values()):
                raise paramiko.SSHException('Pinned vm201 key mismatch')
    if len(pins)!=1:raise RuntimeError('Ambiguous pinned target')
    match=re.fullmatch(r'\[(.+)\]:(\d+)',next(iter(pins.keys())))
    if match is None:raise RuntimeError('Explicit pinned host and port required')
    c=paramiko.SSHClient();c.load_host_keys(str(known));c.set_missing_host_key_policy(PinnedKeyPolicy())
    identity=Path.home()/'.ssh'/'id_ed25519_jumper'
    c.connect(match[1],port=int(match[2]),username=login,password=None,
              pkey=paramiko.Ed25519Key.from_private_key_file(str(identity)),timeout=20,auth_timeout=20,banner_timeout=20,
              look_for_keys=False,allow_agent=False)
    return c

def run_python(client,code,timeout=45):
    payload=base64.b64encode(code.encode()).decode()
    command="python3 -c \"import base64;exec(base64.b64decode('"+payload+"'))\""
    _,out,err=client.exec_command(command,timeout=timeout)
    data=out.read().decode();status=out.channel.recv_exit_status()
    if status:raise RuntimeError('Remote Python failed; error output withheld')
    return json.loads(data)

if __name__=='__main__':
    try:
        c=connect(sys.argv[2] if len(sys.argv)>2 else None)
        try:
            code=Path(sys.argv[1]).read_text(encoding='utf-8')
            print(json.dumps(run_python(c,code),ensure_ascii=True))
        finally:c.close()
    except Exception as e:
        message=str(e).lower()
        flags={label:needle in message for label,needle in {'encrypted_key':'encrypted','unsupported_key':'key format','authentication_failed':'authentication failed','no_auth_method':'no authentication methods','banner_error':'banner','timed_out':'timed out','pin_mismatch':'key mismatch'}.items()}
        print(json.dumps({'status':'blocked','error_type':type(e).__name__,'diagnostic_flags':flags}));sys.exit(1)
