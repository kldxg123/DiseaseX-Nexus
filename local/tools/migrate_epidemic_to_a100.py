from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko


ROOT = Path(r"F:\作业\aibola")
SOURCE_INFO = ROOT / "服务器信息2.txt"
TARGET_INFO = ROOT / "服务器信息a100.txt"
PROJECT = "USER_HOME/autodl-tmp/epidemic-openclaw"


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    host: str
    port: int
    user: str
    password: str


def decode_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = data.decode(encoding)
            if "\x00" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    raise MigrationError(f"SSH_INFO_DECODE_FAILED:{path.name}")


def diagnose_info(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    variants: dict[str, object] = {}
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            variants[encoding] = {"decodes": False}
            continue
        variants[encoding] = {
            "decodes": True,
            "has_nul": "\x00" in text,
            "line_count": len(text.splitlines()),
            "has_ssh_word": bool(re.search(r"ssh", text, re.I)),
            "has_at_sign": "@" in text,
            "has_port_flag": bool(re.search(r"(?:^|\s)-p(?:\s|$)", text, re.I)),
            "has_host_label": bool(re.search(r"(?:host|hostname|主机|服务器)", text, re.I)),
            "has_user_label": bool(re.search(r"(?:user|username|用户|账号)", text, re.I)),
            "has_password_label": bool(re.search(r"(?:password|passwd|密码|密碼)", text, re.I)),
        }
    return {"file_bytes": len(data), "variants": variants}


def credentials(path: Path) -> Credentials:
    text = decode_text(path)
    ssh_line = next(
        (line.strip() for line in text.splitlines() if re.search(r"(?:^|\s)ssh(?:\s|$)", line, re.I)),
        "",
    )
    if ssh_line:
        user_host = re.search(r"(?P<user>[A-Za-z0-9_.-]+)@(?P<host>[^\s]+)", ssh_line)
        port_match = re.search(r"(?:^|\s)-p\s+(?P<port>\d+)(?:\s|$)", ssh_line, re.I)
        if not user_host:
            raise MigrationError(f"SSH_TARGET_NOT_FOUND:{path.name}")
        port = int(port_match.group("port")) if port_match else 22
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        ssh_index = next(i for i, line in enumerate(lines) if line == ssh_line)
        candidates = lines[ssh_index + 1 :]
        if not candidates:
            raise MigrationError(f"SSH_PASSWORD_NOT_FOUND:{path.name}")
        password = re.sub(
            r"^(?:password|passwd|密码|密碼)\s*[:：]\s*",
            "",
            candidates[0],
            flags=re.I,
        )
        if not password:
            raise MigrationError(f"SSH_PASSWORD_EMPTY:{path.name}")
        return Credentials(
            host=user_host.group("host"),
            port=port,
            user=user_host.group("user"),
            password=password,
        )

    fields: dict[str, str] = {}
    label_patterns = {
        "host": r"^(?:host|hostname|ip|server|服务器|服務器|主机|主機|服务器ip|主机ip)$",
        "port": r"^(?:port|端口)$",
        "user": r"^(?:user|username|account|用户|用戶|用户名|用戶名|账号|帳號)$",
        "password": 'REDACTED',
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = re.split(r"\s*[:：=]\s*", line, maxsplit=1)
        if len(parts) != 2:
            parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        label = re.sub(r"[\s_-]+", "", parts[0]).lower()
        for field, pattern in label_patterns.items():
            if re.fullmatch(pattern, label, re.I):
                fields[field] = parts[1].strip()
                break
    required = {"host", "user", "password"}
    if not required.issubset(fields):
        raise MigrationError(f"SSH_LABELED_FIELDS_INCOMPLETE:{path.name}")
    try:
        port = int(fields.get("port", "22"))
    except ValueError as exc:
        raise MigrationError(f"SSH_PORT_INVALID:{path.name}") from exc
    return Credentials(fields["host"], port, fields["user"], fields["password"])


def key_name(creds: Credentials) -> str:
    return creds.host if creds.port == 22 else f"[{creds.host}]:{creds.port}"


def fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def connect_with_gate(label: str, creds: Credentials) -> tuple[paramiko.SSHClient, dict[str, object]]:
    known_hosts = Path(tempfile.gettempdir()) / f"known_hosts_epidemic_migration_{label}"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    if known_hosts.exists():
        host_keys = paramiko.HostKeys(str(known_hosts))
        exact_name = key_name(creds)
        if exact_name in host_keys:
            del host_keys[exact_name]
            host_keys.save(str(known_hosts))
    else:
        known_hosts.touch(mode=0o600)

    first = paramiko.SSHClient()
    first.load_host_keys(str(known_hosts))
    first.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    first.connect(
        creds.host,
        creds.port,
        creds.user,
        creds.password,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
        auth_timeout=30,
        banner_timeout=30,
    )
    first_key = first.get_transport().get_remote_server_key()  # type: ignore[union-attr]
    stdin, stdout, stderr = first.exec_command("id -u; id -un", timeout=20)
    stdin.close()
    identity_lines = stdout.read().decode("utf-8", "replace").strip().splitlines()
    error = stderr.read().decode("utf-8", "replace").strip()
    rc = stdout.channel.recv_exit_status()
    first.close()
    uid = identity_lines[0] if identity_lines else ""
    login_name = identity_lines[1] if len(identity_lines) > 1 else ""
    if rc != 0 or login_name != creds.user:
        raise MigrationError(f"{label.upper()}_IDENTITY_CONFIRMATION_FAILED")

    second = paramiko.SSHClient()
    second.load_host_keys(str(known_hosts))
    second.set_missing_host_key_policy(paramiko.RejectPolicy())
    second.connect(
        creds.host,
        creds.port,
        creds.user,
        creds.password,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
        auth_timeout=30,
        banner_timeout=30,
    )
    second_key = second.get_transport().get_remote_server_key()  # type: ignore[union-attr]
    if second_key.asbytes() != first_key.asbytes():
        second.close()
        raise MigrationError(f"{label.upper()}_HOST_KEY_CHANGED_DURING_GATE")
    return second, {
        "key_type": first_key.get_name(),
        "fingerprint": fingerprint(first_key),
        "stable_second_connection": True,
        "identity": "root" if uid == "0" else "authorized_user",
        "login_stderr_present": bool(error),
    }


def run(
    client: paramiko.SSHClient,
    command: str,
    timeout: int = 300,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if input_text is not None:
        stdin.write(input_text)
        stdin.flush()
    stdin.close()
    output = stdout.read().decode("utf-8", "replace").strip()
    error = stderr.read().decode("utf-8", "replace").strip()
    return stdout.channel.recv_exit_status(), output, error


def checked(
    client: paramiko.SSHClient,
    command: str,
    timeout: int = 300,
    input_text: str | None = None,
) -> str:
    rc, output, error = run(client, command, timeout, input_text)
    if rc:
        raise MigrationError(f"REMOTE_COMMAND_FAILED:{command.split()[0]}:{rc}:{error[-300:]}")
    return output


def q(value: str) -> str:
    return shlex.quote(value)


def remote_python(client: paramiko.SSHClient) -> str:
    command = """for candidate in \
USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python \
USER_HOME/miniconda3/bin/python \
USER_HOME/autodl-tmp/miniconda3/bin/python \
/usr/bin/python3 \
/usr/local/bin/python3; do
  if [ -x \"$candidate\" ]; then printf '%s' \"$candidate\"; exit 0; fi
done
exit 127"""
    return checked(client, command, timeout=30)


def sudo_available(client: paramiko.SSHClient, creds: Credentials) -> bool:
    rc, output, _error = run(
        client,
        "sudo -S -p '' id -u",
        timeout=30,
        input_text=creds.password + "\n",
    )
    return rc == 0 and output.strip() == "0"


def audit(
    client: paramiko.SSHClient,
    role: str,
    sudo_password: str | None = None,
) -> dict[str, object]:
    script = r'''import json, os, pathlib, shutil, subprocess
project=pathlib.Path("USER_HOME/autodl-tmp/epidemic-openclaw")
paths=[
 "USER_HOME/autodl-tmp/epidemic-openclaw",
 "USER_HOME/autodl-tmp/conda/envs/epidemic-core",
 "USER_HOME/autodl-tmp/conda/envs/postgresql18",
 "USER_HOME/autodl-tmp/conda/envs/vllm",
 "USER_HOME/autodl-tmp/epidemic-dashboard-runtime",
 "USER_HOME/autodl-tmp/epidemic-source-review-runtime",
]
def cmd(args):
 try:return subprocess.run(args,text=True,capture_output=True,timeout=30).stdout.strip()
 except Exception:return ""
def size(path):
 try:return int(cmd(["du","-sb",path]).split()[0])
 except Exception:return 0
stat=os.statvfs("USER_HOME/autodl-tmp")
services={}
if project.is_dir():
 candidates={
  "postgres":"scripts/postgres/status.sh",
  "vllm":"scripts/qwen_vllm/status.sh",
  "query_api":"scripts/query_api/status_query_api.sh",
  "retrieval_worker":"scripts/retrieval_worker/status_retrieval_worker.sh",
  "openclaw":"scripts/openclaw/status_openclaw.sh",
  "dashboard":"scripts/dashboard/status_dashboard.sh",
  "discovery_worker":"scripts/source_discovery_worker/status_source_discovery_worker.sh",
  "promotion_worker":"scripts/promotion_worker/status_promotion_worker.sh",
  "review_console":"scripts/source_review/status_source_review.sh",
  "daily_update":"scripts/daily_update/status_daily_update.sh",
 }
 for name,rel in candidates.items():
  f=project/rel
  if f.exists():
   try:services[name]=subprocess.run([str(f)],capture_output=True,timeout=20).returncode==0
   except Exception:services[name]=False
git={"present":(project/".git").exists()}
if git["present"]:
 git["head"]=cmd(["git","-C",str(project),"rev-parse","HEAD"])
 git["branch"]=cmd(["git","-C",str(project),"branch","--show-current"])
 git["dirty_count"]=len(cmd(["git","-C",str(project),"status","--porcelain=v1"]).splitlines())
gpu=cmd(["nvidia-smi","--query-gpu=name,driver_version,memory.total","--format=csv,noheader"])
result={
 "role":os.environ.get("MIGRATION_ROLE","unknown"),
 "arch":cmd(["uname","-m"]),
 "kernel":cmd(["uname","-r"]),
 "gpu":gpu.splitlines(),
 "data_disk_free_bytes":stat.f_bavail*stat.f_frsize,
 "paths":{p:{"exists":pathlib.Path(p).exists(),"bytes":size(p) if pathlib.Path(p).exists() else 0} for p in paths},
 "commands":{x:bool(shutil.which(x)) for x in ("tar","zstd","rsync","sha256sum")},
 "git":git,
 "services":services,
}
print(json.dumps(result))
'''
    python = remote_python(client)
    base = f"env MIGRATION_ROLE={q(role)} {q(python)} -c {q(script)}"
    if sudo_password is None:
        return json.loads(checked(client, base, timeout=180))
    command = "sudo -S -p '' " + base
    return json.loads(
        checked(client, command, timeout=180, input_text=sudo_password + "\n")
    )


def audit_user_target(client: paramiko.SSHClient) -> dict[str, object]:
    script = r'''import json, os, pathlib, shutil, socket, subprocess
home=pathlib.Path.home()
root=home/"autodl-tmp"
project=root/"epidemic-openclaw"
def cmd(args):
 try:return subprocess.run(args,text=True,capture_output=True,timeout=30).stdout.strip()
 except Exception:return ""
def size(path):
 try:return int(cmd(["du","-sb",str(path)]).split()[0])
 except Exception:return 0
stat=os.statvfs(str(home))
gpu=cmd(["nvidia-smi","--query-gpu=name,driver_version,memory.total","--format=csv,noheader"])
ports={}
for port in (5432,8000,8010,18789,8020,8021):
 s=socket.socket()
 try:
  s.bind(("127.0.0.1",port));ports[str(port)]=True
 except OSError:ports[str(port)]=False
 finally:s.close()
print(json.dumps({
 "arch":cmd(["uname","-m"]),
 "kernel":cmd(["uname","-r"]),
 "gpu":gpu.splitlines(),
 "home_free_bytes":stat.f_bavail*stat.f_frsize,
 "deployment_root_exists":root.exists(),
 "deployment_root_bytes":size(root) if root.exists() else 0,
 "project_exists":project.exists(),
 "project_bytes":size(project) if project.exists() else 0,
 "home_writable":os.access(home,os.W_OK|os.X_OK),
 "commands":{x:bool(shutil.which(x)) for x in ("tar","zstd","rsync","sha256sum","git","curl","gcc","make")},
 "ports_available":ports,
}))
'''
    python = remote_python(client)
    return json.loads(checked(client, f"{q(python)} -c {q(script)}", timeout=180))


def source_layout(client: paramiko.SSHClient) -> dict[str, object]:
    script = r'''import json, os, pathlib, re, subprocess
project=pathlib.Path("USER_HOME/autodl-tmp/epidemic-openclaw")
data_root=pathlib.Path("USER_HOME/autodl-tmp")
def cmd(args,timeout=120):
 try:return subprocess.run(args,text=True,capture_output=True,timeout=timeout).stdout.strip()
 except Exception:return ""
def size(path):
 try:return int(cmd(["du","-sb",str(path)]).split()[0])
 except Exception:return 0
top={p.name:size(p) for p in project.iterdir() if p.name not in {".git"}}
storage=project/"storage"
storage_sizes={p.name:size(p) for p in storage.iterdir()} if storage.exists() else {}
data_dirs={p.name:size(p) for p in data_root.iterdir() if p.is_dir()}
symlinks=[]
for p in data_root.rglob("*"):
 try:
  if p.is_symlink():symlinks.append({"path":str(p),"target":os.readlink(p)})
 except (OSError,PermissionError):pass
 if len(symlinks)>=200:break
excluded={".git","node_modules","cache","models","database","raw","logs","run","snapshots","artifacts","__pycache__"}
path_files=[];path_values=set()
for p in project.rglob("*"):
 if not p.is_file() or any(part in excluded for part in p.parts):continue
 try:
  if p.stat().st_size>5_000_000:continue
  raw=p.read_bytes()
  if b"\x00" in raw or b"USER_HOME/autodl-tmp" not in raw:continue
  text=raw.decode("utf-8")
 except (OSError,UnicodeError):continue
 path_files.append(str(p.relative_to(project)))
 path_values.update(re.findall(r"USER_HOME/autodl-tmp/[A-Za-z0-9_./-]+",text))
envs={}
for name in ("epidemic-core","postgresql18"):
 env=pathlib.Path("USER_HOME/autodl-tmp/conda/envs")/name
 envs[name]={
  "exists":env.exists(),
  "conda_unpack":(env/"bin/conda-unpack").exists(),
  "python":(env/"bin/python").exists(),
  "prefix_occurrences":int(cmd(["grep","-RIl","--exclude-dir=__pycache__","USER_HOME/autodl-tmp",str(env)]).count("\n")+1) if env.exists() else 0,
 }
print(json.dumps({
 "project_top_bytes":dict(sorted(top.items(),key=lambda x:x[1],reverse=True)),
 "storage_bytes":dict(sorted(storage_sizes.items(),key=lambda x:x[1],reverse=True)),
 "data_root_bytes":dict(sorted(data_dirs.items(),key=lambda x:x[1],reverse=True)),
 "symlinks":symlinks,
 "hardcoded_project_files":sorted(path_files),
 "hardcoded_path_values":sorted(path_values),
 "envs":envs,
}))
'''
    python = remote_python(client)
    return json.loads(checked(client, f"{q(python)} -c {q(script)}", timeout=600))


def layout_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    source, gate = connect_with_gate("source", source_creds)
    try:
        print(
            json.dumps(
                {"source_gate": gate, "layout": source_layout(source)},
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        source.close()


def runtime_manifest(client: paramiko.SSHClient) -> dict[str, object]:
    script = r'''import json, pathlib, re, subprocess
project=pathlib.Path("USER_HOME/autodl-tmp/epidemic-openclaw")
roots=[project/"scripts",project/"config",project/"src",project/"migrations",project/"tests"]
files=[];values=set()
for root in roots:
 if not root.exists():continue
 for p in root.rglob("*"):
  if not p.is_file():continue
  try:
   raw=p.read_bytes()
   if b"\x00" in raw or b"USER_HOME/autodl-tmp" not in raw:continue
   text=raw.decode("utf-8")
  except (OSError,UnicodeError):continue
  files.append(str(p.relative_to(project)))
  values.update(re.findall(r"USER_HOME/autodl-tmp/[A-Za-z0-9_./-]+",text))
def cmd(args,timeout=120):
 try:return subprocess.run(args,text=True,capture_output=True,timeout=timeout).stdout.strip()
 except Exception:return ""
def size(p):
 try:return int(cmd(["du","-sb",str(p)]).split()[0])
 except Exception:return 0
env_roots=[]
for base in (pathlib.Path("USER_HOME/autodl-tmp/conda/envs"),pathlib.Path("USER_HOME/miniconda3/envs"),pathlib.Path("USER_HOME/autodl-tmp/miniconda3/envs")):
 if base.exists():
  env_roots.extend({"base":str(base),"name":p.name,"bytes":size(p)} for p in base.iterdir() if p.is_dir())
vllm_bins=cmd(["find","USER_HOME/autodl-tmp","USER_HOME/miniconda3","-type","f","-path","*/bin/vllm","-print"],300).splitlines()
print(json.dumps({
 "hardcoded_files":sorted(files),
 "hardcoded_values":sorted(values),
 "environment_directories":env_roots,
 "vllm_bins":vllm_bins,
}))
'''
    python = remote_python(client)
    return json.loads(checked(client, f"{q(python)} -c {q(script)}", timeout=600))


def runtime_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    source, gate = connect_with_gate("source", source_creds)
    try:
        print(json.dumps({"source_gate": gate, "runtime": runtime_manifest(source)}, indent=2))
    finally:
        source.close()


def run_long(
    client: paramiko.SSHClient,
    command: str,
    label: str,
    timeout: int,
) -> None:
    transport = client.get_transport()
    if transport is None:
        raise MigrationError(f"{label}_NO_TRANSPORT")
    channel = transport.open_session()
    channel.exec_command(command)
    started = time.monotonic()
    last_notice = started
    stderr_tail = bytearray()
    stdout_tail = bytearray()
    while not channel.exit_status_ready():
        if channel.recv_ready():
            stdout_tail.extend(channel.recv(65536))
            del stdout_tail[:-2048]
        if channel.recv_stderr_ready():
            stderr_tail.extend(channel.recv_stderr(65536))
            del stderr_tail[:-2048]
        now = time.monotonic()
        if now - started > timeout:
            channel.close()
            raise MigrationError(f"{label}_TIMEOUT")
        if now - last_notice >= 20:
            print(
                json.dumps({"stage": label, "elapsed_seconds": int(now - started)}),
                flush=True,
            )
            last_notice = now
        time.sleep(0.1)
    while channel.recv_ready():
        stdout_tail.extend(channel.recv(65536))
        del stdout_tail[:-2048]
    while channel.recv_stderr_ready():
        stderr_tail.extend(channel.recv_stderr(65536))
        del stderr_tail[:-2048]
    rc = channel.recv_exit_status()
    channel.close()
    if rc:
        raise MigrationError(f"{label}_FAILED:{rc}")
    print(json.dumps({"stage": label, "status": "complete"}), flush=True)


def stream_file(
    source: paramiko.SSHClient,
    source_path: str,
    target: paramiko.SSHClient,
    target_path: str,
    label: str,
    timeout: int = 7200,
) -> int:
    expected = int(checked(source, f"stat -c %s {q(source_path)}", timeout=30))
    target_dir = str(Path(target_path).parent).replace("\\", "/")
    checked(target, f"mkdir -p {q(target_dir)} && chmod 700 {q(target_dir)}")
    source_transport = source.get_transport()
    target_transport = target.get_transport()
    if source_transport is None or target_transport is None:
        raise MigrationError(f"{label}_NO_TRANSPORT")
    source_channel = source_transport.open_session()
    target_channel = target_transport.open_session()
    source_channel.exec_command(f"cat {q(source_path)}")
    target_channel.exec_command(f"umask 077; cat > {q(target_path)}")
    copied = 0
    started = time.monotonic()
    last_notice = started
    source_error = bytearray()
    target_error = bytearray()
    while True:
        progressed = False
        if source_channel.recv_ready():
            data = source_channel.recv(4 * 1024 * 1024)
            if data:
                target_channel.sendall(data)
                copied += len(data)
                progressed = True
        if source_channel.recv_stderr_ready():
            source_error.extend(source_channel.recv_stderr(65536))
            del source_error[:-2048]
        if target_channel.recv_stderr_ready():
            target_error.extend(target_channel.recv_stderr(65536))
            del target_error[:-2048]
        if source_channel.exit_status_ready() and not source_channel.recv_ready():
            break
        now = time.monotonic()
        if now - started > timeout:
            source_channel.close()
            target_channel.close()
            raise MigrationError(f"{label}_TIMEOUT")
        if now - last_notice >= 20:
            print(
                json.dumps(
                    {
                        "stage": label,
                        "copied_bytes": copied,
                        "expected_bytes": expected,
                    }
                ),
                flush=True,
            )
            last_notice = now
        if not progressed:
            time.sleep(0.01)
    source_rc = source_channel.recv_exit_status()
    target_channel.shutdown_write()
    while not target_channel.exit_status_ready():
        if target_channel.recv_stderr_ready():
            target_error.extend(target_channel.recv_stderr(65536))
            del target_error[:-2048]
        if time.monotonic() - started > timeout:
            target_channel.close()
            raise MigrationError(f"{label}_TARGET_TIMEOUT")
        time.sleep(0.05)
    target_rc = target_channel.recv_exit_status()
    source_channel.close()
    target_channel.close()
    actual = int(checked(target, f"stat -c %s {q(target_path)}", timeout=30))
    if source_rc or target_rc or copied != expected or actual != expected:
        raise MigrationError(f"{label}_STREAM_INTEGRITY_FAILED")
    print(
        json.dumps({"stage": label, "status": "complete", "bytes": copied}),
        flush=True,
    )
    return copied


def setup_direct_transfer(
    source: paramiko.SSHClient,
    target: paramiko.SSHClient,
    target_creds: Credentials,
) -> dict[str, str]:
    token = secrets.token_hex(12)
    key_path = f"/tmp/epidemic-migration-{token}"
    known_hosts = f"/tmp/epidemic-known-hosts-{token}"
    comment = f"epidemic-migration-{token}"
    target_key = target.get_transport().get_remote_server_key()  # type: ignore[union-attr]
    host_entry = (
        f"{key_name(target_creds)} {target_key.get_name()} {target_key.get_base64()}\n"
    )
    sftp = source.open_sftp()
    try:
        with sftp.file(known_hosts, "w") as handle:
            handle.write(host_entry)
        sftp.chmod(known_hosts, 0o600)
    finally:
        sftp.close()
    checked(
        source,
        f"ssh-keygen -q -t ed25519 -N '' -C {q(comment)} -f {q(key_path)} && "
        f"chmod 600 {q(key_path)} {q(known_hosts)}",
        timeout=60,
    )
    public_key = checked(source, f"cat {q(key_path + '.pub')}", timeout=30).strip()
    if comment not in public_key:
        raise MigrationError("EPHEMERAL_PUBLIC_KEY_INVALID")
    rc, _output, _error = run(
        target,
        "umask 077; mkdir -p \"$HOME/.ssh\"; chmod 700 \"$HOME/.ssh\"; "
        "touch \"$HOME/.ssh/authorized_keys\"; chmod 600 \"$HOME/.ssh/authorized_keys\"; "
        "cat >> \"$HOME/.ssh/authorized_keys\"",
        timeout=30,
        input_text=public_key + "\n",
    )
    if rc:
        raise MigrationError("EPHEMERAL_KEY_INSTALL_FAILED")
    ssh_command = (
        f"ssh -p {target_creds.port} -i {q(key_path)} -o BatchMode=yes "
        f"-o IdentitiesOnly=yes -o UserKnownHostsFile={q(known_hosts)} "
        "-o StrictHostKeyChecking=yes -o ConnectTimeout=20"
    )
    remote = f"{target_creds.user}@{target_creds.host}"
    rc, output, _error = run(
        source,
        f"{ssh_command} {q(remote)} id -un",
        timeout=60,
    )
    if rc or output.strip() != target_creds.user:
        raise MigrationError("DIRECT_SERVER_TRANSFER_GATE_FAILED")
    print(
        json.dumps({"stage": "direct_server_transfer_gate", "status": "complete"}),
        flush=True,
    )
    return {
        "key_path": key_path,
        "known_hosts": known_hosts,
        "public_key": public_key,
        "ssh_command": ssh_command,
        "remote": remote,
    }


def cleanup_direct_transfer(
    source: paramiko.SSHClient,
    target: paramiko.SSHClient,
    context: dict[str, str],
) -> None:
    python = remote_python(target)
    code = r'''import pathlib,sys
p=pathlib.Path.home()/".ssh"/"authorized_keys"
key=sys.stdin.read().strip()
if p.exists():
 lines=[line for line in p.read_text().splitlines() if line.strip()!=key]
 p.write_text(("\n".join(lines)+"\n") if lines else "")
 p.chmod(0o600)
'''
    run(
        target,
        f"{q(python)} -c {q(code)}",
        timeout=30,
        input_text=context["public_key"] + "\n",
    )
    run(
        source,
        f"rm -f {q(context['key_path'])} {q(context['key_path'] + '.pub')} "
        f"{q(context['known_hosts'])}",
        timeout=30,
    )
    print(json.dumps({"stage": "ephemeral_transfer_access", "status": "removed"}), flush=True)


def direct_copy_file(
    source: paramiko.SSHClient,
    source_path: str,
    target: paramiko.SSHClient,
    target_path: str,
    context: dict[str, str],
    label: str,
    timeout: int = 7200,
) -> int:
    expected = int(checked(source, f"stat -c %s {q(source_path)}", timeout=30))
    target_dir = target_path.rsplit("/", 1)[0]
    checked(target, f"mkdir -p {q(target_dir)} && chmod 700 {q(target_dir)}")
    remote_arg = context["remote"] + ":" + target_path
    command = (
        "rsync -a --partial --inplace --chmod=F600 "
        f"-e {q(context['ssh_command'])} {q(source_path)} {q(remote_arg)}"
    )
    run_long(source, command, label, timeout)
    actual = int(checked(target, f"stat -c %s {q(target_path)}", timeout=30))
    if actual != expected:
        raise MigrationError(f"{label}_SIZE_MISMATCH")
    print(
        json.dumps({"stage": label, "status": "verified", "bytes": actual}),
        flush=True,
    )
    return actual


def direct_copy_parallel(
    source: paramiko.SSHClient,
    source_path: str,
    target: paramiko.SSHClient,
    target_path: str,
    context: dict[str, str],
    label: str,
    timeout: int = 7200,
    parallelism: int = 8,
) -> int:
    expected = int(checked(source, f"stat -c %s {q(source_path)}", timeout=30))
    source_parts = source_path + ".parts"
    target_parts = target_path + ".parts"
    target_dir = target_path.rsplit("/", 1)[0]
    checked(
        source,
        f"rm -rf {q(source_parts)} && mkdir -p {q(source_parts)} && "
        f"split -b 64M -d -a 5 {q(source_path)} {q(source_parts + '/part-')}",
        timeout=3600,
    )
    checked(
        target,
        f"rm -rf {q(target_parts)} && mkdir -p {q(target_parts)} {q(target_dir)} && "
        f"chmod 700 {q(target_parts)} {q(target_dir)}",
        timeout=120,
    )
    destination = context["remote"] + ":" + target_parts + "/"
    transfer_one = (
        "rsync -a --partial --inplace --chmod=F600 "
        f"-e {q(context['ssh_command'])} \"$1\" {q(destination)}"
    )
    command = (
        f"find {q(source_parts)} -maxdepth 1 -type f -name 'part-*' -print0 | "
        f"xargs -0 -n1 -P{parallelism} sh -c {q(transfer_one)} sh"
    )
    run_long(source, command, label, timeout)
    checked(
        target,
        f"cat {q(target_parts)}" + "/part-* > " + q(target_path),
        timeout=3600,
    )
    actual = int(checked(target, f"stat -c %s {q(target_path)}", timeout=30))
    if actual != expected:
        raise MigrationError(f"{label}_SIZE_MISMATCH")
    checked(source, f"rm -rf {q(source_parts)}", timeout=300)
    checked(target, f"rm -rf {q(target_parts)}", timeout=300)
    print(
        json.dumps({"stage": label, "status": "verified", "bytes": actual}),
        flush=True,
    )
    return actual


def sha256(client: paramiko.SSHClient, path: str) -> str:
    value = checked(client, f"sha256sum {q(path)} | awk '{{print $1}}'", timeout=3600)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise MigrationError("SHA256_OUTPUT_INVALID")
    return value


def relocate_target(
    target: paramiko.SSHClient,
    target_root: str,
    target_project: str,
) -> dict[str, object]:
    script = r'''import json, os, pathlib
old=b"USER_HOME/autodl-tmp"
root=pathlib.Path(os.environ["TARGET_DATA_ROOT"])
project=pathlib.Path(os.environ["TARGET_PROJECT"])
new=str(root).encode()
skip={".git","models","cache","__pycache__",".mypy_cache",".pytest_cache",".ruff_cache"}
rewritten=[]
scan_roots=[project,root/"epidemic-dashboard-runtime",root/"epidemic-source-review-runtime",root/"epidemic-extractor-runtime"]
for scan_root in scan_roots:
 if not scan_root.exists():continue
 for p in scan_root.rglob("*"):
  if any(part in skip for part in p.parts):continue
  try:
   if not p.is_file() or p.stat().st_size>64_000_000:continue
   data=p.read_bytes()
  except (OSError,PermissionError):continue
  if old not in data or b"\x00" in data:continue
  try:data.decode("utf-8")
  except UnicodeDecodeError:continue
  p.write_bytes(data.replace(old,new));rewritten.append(str(p.relative_to(root)))
links=[]
for p in root.rglob("*"):
 try:
  if not p.is_symlink():continue
  value=os.readlink(p)
  if value.startswith(old.decode()):
   p.unlink();p.symlink_to(value.replace(old.decode(),str(root),1));links.append(str(p.relative_to(root)))
 except (OSError,PermissionError):continue
secrets=[project/".secrets",project/"storage/secrets"]
for directory in secrets:
 if directory.exists():
  directory.chmod(0o700)
  for child in directory.iterdir():
   if child.is_file():child.chmod(0o600)
print(json.dumps({"rewritten_file_count":len(rewritten),"rewritten_link_count":len(links)}))
'''
    python = remote_python(target)
    command = (
        f"TARGET_DATA_ROOT={q(target_root)} TARGET_PROJECT={q(target_project)} "
        f"{q(python)} -c {q(script)}"
    )
    return json.loads(checked(target, command, timeout=1800))


def migrate_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    source.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    target.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    source_project = PROJECT
    source_data_root = "USER_HOME/autodl-tmp"
    source_env_root = source_data_root + "/conda/envs"
    target_home = checked(target, "printf '%s' \"$HOME\"", timeout=30)
    if not target_home.startswith("/") or target_home == "/":
        raise MigrationError("TARGET_HOME_INVALID")
    target_root = target_home.rstrip("/") + "/autodl-tmp"
    target_project = target_root + "/epidemic-openclaw"
    staging = target_root + "/.migration-staging"
    target_system_python = remote_python(target)
    source_tmp = source_data_root + "/epidemic-migration-staging"
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    total_bytes = 0
    archive_hashes: dict[str, str] = {}
    source_stopped = False
    direct_context: dict[str, str] | None = None
    try:
        print(
            json.dumps(
                {
                    "stage": "migration_gate",
                    "source_key_type": source_gate["key_type"],
                    "target_key_type": target_gate["key_type"],
                    "stable_second_connections": True,
                    "target_user_directory_deployment": True,
                }
            ),
            flush=True,
        )
        prepare = f"""set -eu
umask 077
mkdir -p {q(target_root)} {q(staging)}
for item in epidemic-openclaw epidemic-dashboard-runtime epidemic-source-review-runtime epidemic-extractor-runtime; do
  if [ -e {q(target_root)}/\"$item\" ]; then
    mv {q(target_root)}/\"$item\" {q(target_root)}/\"$item.pre-migration-{timestamp}\"
  fi
done
mkdir -p {q(target_root + '/conda/envs')}
for item in epidemic-core postgresql18 qwen-vllm; do
  if [ -e {q(target_root + '/conda/envs')}/\"$item\" ]; then
    mv {q(target_root + '/conda/envs')}/\"$item\" {q(target_root + '/conda/envs')}/\"$item.pre-migration-{timestamp}\"
  fi
done"""
        checked(target, prepare, timeout=120)
        checked(source, f"mkdir -p {q(source_tmp)} && chmod 700 {q(source_tmp)}")
        direct_context = setup_direct_transfer(source, target, target_creds)

        tools_dir = source_tmp + "/conda-pack-tools"
        core_python = source_env_root + "/epidemic-core/bin/python"
        import_rc, _out, _err = run(
            source,
            f"PYTHONPATH={q(tools_dir)} {q(core_python)} -c 'import conda_pack'",
            timeout=30,
        )
        if import_rc:
            pip_command = (
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"mkdir -p {q(tools_dir)}; "
                f"{q(core_python)} -m pip install --disable-pip-version-check --no-input "
                f"--timeout 60 --retries 2 --cache-dir {q(source_project + '/storage/cache/pip')} "
                f"--upgrade --target {q(tools_dir)} conda-pack==0.8.1 setuptools==80.9.0"
            )
            run_long(source, pip_command, "install_conda_pack", 900)

        for env_name in ("epidemic-core", "postgresql18", "qwen-vllm"):
            source_env = source_env_root + "/" + env_name
            source_archive = source_tmp + f"/{env_name}.tar.gz"
            target_archive = staging + f"/{env_name}.tar.gz"
            pack_code = (
                "from conda_pack import pack;"
                f"pack(prefix={source_env!r},output={source_archive!r},force=True,"
                "ignore_missing_files=True,ignore_editable_packages=True)"
            )
            run_long(
                source,
                f"PYTHONPATH={q(tools_dir)} {q(core_python)} -c {q(pack_code)}",
                f"pack_{env_name}",
                7200,
            )
            source_hash = sha256(source, source_archive)
            total_bytes += direct_copy_parallel(
                source,
                source_archive,
                target,
                target_archive,
                direct_context,
                f"transfer_{env_name}",
                3600,
            )
            target_hash = sha256(target, target_archive)
            if source_hash != target_hash:
                raise MigrationError(f"{env_name.upper()}_ARCHIVE_HASH_MISMATCH")
            archive_hashes[env_name] = source_hash
            target_env = target_root + "/conda/envs/" + env_name
            extract = (
                f"mkdir -p {q(target_env)} && "
                f"tar -xzf {q(target_archive)} -C {q(target_env)} --no-same-owner && "
                f"PATH={q(target_env + '/bin')}:\"$PATH\" {q(target_env + '/bin/conda-unpack')}"
            )
            run_long(target, extract, f"extract_relocate_{env_name}", 3600)
            checked(
                source,
                f"rm -f {q(source_archive)}",
                timeout=60,
            )
            checked(
                target,
                f"rm -f {q(target_archive)}",
                timeout=60,
            )

        stop_command = q(source_project + "/scripts/services/stop_all.sh")
        run_long(source, stop_command, "stop_source_for_consistent_snapshot", 900)
        source_stopped = True
        project_archive = source_tmp + "/epidemic-project.tar"
        tar_command = (
            f"tar --exclude='epidemic-openclaw/storage/run/*' "
            f"--exclude='epidemic-openclaw/storage/cache' "
            f"--exclude='epidemic-openclaw/storage/tmp' "
            f"--exclude='epidemic-openclaw/storage/tmp/migration-*' "
            f"-cf {q(project_archive)} -C {q(source_data_root)} "
            "epidemic-openclaw epidemic-dashboard-runtime "
            "epidemic-source-review-runtime epidemic-extractor-runtime"
        )
        run_long(source, tar_command, "create_consistent_project_archive", 3600)
        run_long(
            source,
            q(source_project + "/scripts/services/start_all.sh"),
            "restore_source_services",
            1800,
        )
        source_stopped = False
        source_hash = sha256(source, project_archive)
        target_archive = staging + "/epidemic-project.tar"
        total_bytes += direct_copy_parallel(
            source,
            project_archive,
            target,
            target_archive,
            direct_context,
            "transfer_project_snapshot",
            7200,
        )
        target_hash = sha256(target, target_archive)
        if source_hash != target_hash:
            raise MigrationError("PROJECT_ARCHIVE_HASH_MISMATCH")
        archive_hashes["project"] = source_hash
        run_long(
            target,
            f"tar -xf {q(target_archive)} -C {q(target_root)} --no-same-owner",
            "extract_project_snapshot",
            3600,
        )
        checked(
            target,
            f"mkdir -p {q(target_project + '/storage/cache/huggingface')} "
            f"{q(target_project + '/storage/cache/modelscope')} "
            f"{q(target_project + '/storage/cache/pip')} "
            f"{q(target_project + '/storage/cache/torch')} "
            f"{q(target_project + '/storage/cache/xdg')} "
            f"{q(target_project + '/storage/tmp')}",
            timeout=60,
        )
        checked(source, f"rm -f {q(project_archive)}", timeout=60)
        checked(target, f"rm -f {q(target_archive)}", timeout=60)

        relocation = relocate_target(target, target_root, target_project)
        checked(
            target,
            f"find {q(target_project + '/storage')} -type f "
            "\\( -name '*.pid' -o -name '*.sock' -o -name ready_epoch -o -name start_epoch \\) -delete",
            timeout=300,
        )
        print(
            json.dumps(
                {
                    "stage": "migration_complete",
                    "transferred_bytes": total_bytes,
                    "archive_sha256": archive_hashes,
                    "relocation": relocation,
                }
            ),
            flush=True,
        )
    finally:
        if source_stopped:
            try:
                run_long(
                    source,
                    q(source_project + "/scripts/services/start_all.sh"),
                    "emergency_restore_source_services",
                    1800,
                )
            except Exception:
                print(json.dumps({"stage": "source_restore", "status": "failed"}), flush=True)
        if direct_context is not None:
            try:
                cleanup_direct_transfer(source, target, direct_context)
            except Exception:
                print(
                    json.dumps(
                        {"stage": "ephemeral_transfer_access", "status": "cleanup_failed"}
                    ),
                    flush=True,
                )
        source.close()
        target.close()


def pack_diagnose_action() -> None:
    creds = credentials(SOURCE_INFO)
    client, gate = connect_with_gate("source", creds)
    try:
        tools_dir = "USER_HOME/autodl-tmp/epidemic-migration-staging/conda-pack-tools"
        python = "USER_HOME/autodl-tmp/conda/envs/epidemic-core/bin/python"
        code = "import conda_pack,inspect,json;print(json.dumps({'version':getattr(conda_pack,'__version__','unknown'),'signature':str(inspect.signature(conda_pack.pack))}))"
        result = json.loads(
            checked(client, f"PYTHONPATH={q(tools_dir)} {q(python)} -c {q(code)}")
        )
        print(json.dumps({"source_gate": gate, "conda_pack": result}, indent=2))
    finally:
        client.close()


def cleanup_stale_direct_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        target_python = remote_python(target)
        code = r'''import pathlib
p=pathlib.Path.home()/".ssh"/"authorized_keys"
removed=0
if p.exists():
 lines=p.read_text().splitlines()
 kept=[]
 for line in lines:
  if " epidemic-migration-" in line:removed+=1
  else:kept.append(line)
 p.write_text(("\n".join(kept)+"\n") if kept else "")
 p.chmod(0o600)
print(removed)
'''
        removed = int(checked(target, f"{q(target_python)} -c {q(code)}"))
        checked(
            source,
            "find /tmp -maxdepth 1 -type f "
            "\\( -name 'epidemic-migration-*' -o -name 'epidemic-known-hosts-*' \\) -delete",
        )
        print(
            json.dumps(
                {
                    "source_gate": source_gate,
                    "target_gate": target_gate,
                    "stale_authorized_keys_removed": removed,
                    "source_temporary_keys_removed": True,
                }
            )
        )
    finally:
        source.close()
        target.close()


def rebuild_manifest_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        source_python = "USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python"
        code = r'''import json, pathlib, subprocess
wanted={"vllm","torch","torchvision","torchaudio","transformers","huggingface-hub","tokenizers","xformers","flashinfer-python","compressed-tensors","numpy"}
packages=json.loads(subprocess.run(["USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python","-m","pip","list","--format=json"],text=True,capture_output=True,check=True).stdout)
versions={p["name"]:p["version"] for p in packages if p["name"].lower() in wanted}
model=pathlib.Path("USER_HOME/autodl-tmp/epidemic-openclaw/models/llm/Qwen3-8B-AWQ")
files=[]
for p in model.rglob("*"):
 if p.is_file() and ".cache" not in p.parts:
  files.append({"path":str(p.relative_to(model)),"bytes":p.stat().st_size})
print(json.dumps({"python":subprocess.run(["USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python","--version"],text=True,capture_output=True).stdout.strip(),"packages":versions,"model_files":files,"model_bytes":sum(x["bytes"] for x in files)}))
'''
        source_manifest = json.loads(
            checked(source, f"{q(source_python)} -c {q(code)}", timeout=300)
        )
        target_script = r'''import json, pathlib, shutil, subprocess
def version(cmd):
 try:
  p=subprocess.run(cmd,text=True,capture_output=True,timeout=20)
  return (p.stdout or p.stderr).strip().splitlines()[0]
 except Exception:return ""
print(json.dumps({
 "python3":version(["python3","--version"]),
 "pip3":version(["python3","-m","pip","--version"]),
 "conda_present":bool(shutil.which("conda")),
 "mamba_present":bool(shutil.which("mamba")),
 "micromamba_present":bool(shutil.which("micromamba")),
 "uv_present":bool(shutil.which("uv")),
 "network_turbo_readable":pathlib.Path("/etc/network_turbo").is_file() and os.access("/etc/network_turbo",os.R_OK) if False else pathlib.Path("/etc/network_turbo").is_file(),
}))
'''
        target_python = remote_python(target)
        target_manifest = json.loads(
            checked(target, f"{q(target_python)} -c {q(target_script)}", timeout=60)
        )
        print(
            json.dumps(
                {
                    "source_gate": source_gate,
                    "target_gate": target_gate,
                    "source_runtime": source_manifest,
                    "target_runtime": target_manifest,
                },
                indent=2,
            )
        )
    finally:
        source.close()
        target.close()


def source_provenance_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        source_python = "USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python"
        script = r'''import json,pathlib,re,urllib.parse
project=pathlib.Path("USER_HOME/autodl-tmp/epidemic-openclaw")
selected=[]
for base in (project/"config",project/"requirements",project/"reports/p0_3"):
 if not base.exists():continue
 for p in base.rglob("*"):
  if not p.is_file() or p.stat().st_size>2_000_000:continue
  try:text=p.read_text()
  except UnicodeError:continue
  for line in text.splitlines():
   if re.search(r"qwen|vllm|torch|cu129|huggingface|revision|sha-?256|miniforge|conda",line,re.I):
    if not re.search(r"proxy|password|token|secret",line,re.I):
     selected.append({"file":str(p.relative_to(project)),"line":line[:500]})
direct={}
site=pathlib.Path("USER_HOME/autodl-tmp/conda/envs/qwen-vllm/lib/python3.12/site-packages")
for p in site.glob("*.dist-info/direct_url.json"):
 name=p.parent.name.split("-")[0].lower()
 if name not in {"vllm","torch","torchvision","torchaudio","flashinfer_python"}:continue
 try:
  obj=json.loads(p.read_text());u=urllib.parse.urlsplit(obj.get("url",""))
  direct[name]={"domain":u.hostname or "","filename":pathlib.PurePosixPath(u.path).name}
 except Exception:pass
commits=set()
model=project/"models/llm/Qwen3-8B-AWQ/.cache/huggingface/download"
if model.exists():
 for p in model.rglob("*.metadata"):
  try:
   first=p.read_text().splitlines()[0].strip()
   if re.fullmatch(r"[0-9a-f]{40}",first):commits.add(first)
  except Exception:pass
print(json.dumps({"selected_config_lines":selected,"direct_sources":direct,"model_commits":sorted(commits)}))
'''
        provenance = json.loads(
            checked(source, f"{q(source_python)} -c {q(script)}", timeout=300)
        )
        probe = r'''set +e
for name in github huggingface pypi vllm_wheels; do
 case "$name" in
  github) url=https://github.com/conda-forge/miniforge/releases/;;
  huggingface) url=https://huggingface.co/;;
  pypi) url=https://pypi.org/simple/;;
  vllm_wheels) url=https://wheels.vllm.ai/;;
 esac
 code=$(curl -LIsS --max-time 20 -o /dev/null -w '%{http_code}' "$url")
 rc=$?
 if [ "$rc" -eq 0 ]; then printf '%s=%s\n' "$name" "$code"; else printf '%s=unavailable\n' "$name"; fi
done'''
        raw = checked(target, probe, timeout=120)
        network = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        print(
            json.dumps(
                {
                    "source_gate": source_gate,
                    "target_gate": target_gate,
                    "provenance": provenance,
                    "target_network": network,
                },
                indent=2,
            )
        )
    finally:
        source.close()
        target.close()


def rebuild_probe_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        wheel_url = (
            "https://github.com/vllm-project/vllm/releases/download/v0.24.0/"
            "vllm-0.24.0%2Bcu129-cp38-abi3-manylinux_2_28_x86_64.whl"
        )
        urls = {
            "pypa_bootstrap": "https://bootstrap.pypa.io/get-pip.py",
            "modelscope": "https://modelscope.cn/",
            "modelscope_api": "https://modelscope.cn/api/v1/models/Qwen/Qwen3-8B-AWQ",
            "vllm_release_wheel": wheel_url,
        }
        probes: dict[str, object] = {}
        for name, url in urls.items():
            rc, output, _error = run(
                target,
                f"curl -LIsS --max-time 30 -o /dev/null -w '%{{http_code}} %{{size_download}}' {q(url)}",
                timeout=45,
            )
            probes[name] = {"curl_rc": rc, "result": output if rc == 0 else "unavailable"}
        venv_rc, _out, _err = run(
            target,
            "d=$(mktemp -d); python3 -m venv --without-pip \"$d/venv\"; rc=$?; "
            "test -x \"$d/venv/bin/python\"; verify=$?; rm -rf \"$d\"; "
            "test \"$rc\" -eq 0 -a \"$verify\" -eq 0",
            timeout=60,
        )
        cache = checked(
            source,
            "find USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/pip "
            "-type f -size +100M -printf '%s %f\\n' 2>/dev/null | sort -nr | head -n 20",
            timeout=300,
        )
        cache_sizes = []
        for line in cache.splitlines():
            size, _name = line.split(" ", 1)
            cache_sizes.append(int(size))
        print(
            json.dumps(
                {
                    "source_gate": source_gate,
                    "target_gate": target_gate,
                    "target_probes": probes,
                    "target_venv_without_pip": venv_rc == 0,
                    "source_large_pip_cache_file_sizes": cache_sizes,
                },
                indent=2,
            )
        )
    finally:
        source.close()
        target.close()


def migrate_state_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    source.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    target.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    source_root = "USER_HOME/autodl-tmp"
    source_project = PROJECT
    source_envs = source_root + "/conda/envs"
    source_tmp = source_root + "/epidemic-migration-staging"
    target_home = checked(target, "printf '%s' \"$HOME\"")
    if not target_home.startswith("/") or target_home == "/":
        raise MigrationError("TARGET_HOME_INVALID")
    target_root = target_home.rstrip("/") + "/autodl-tmp"
    target_project = target_root + "/epidemic-openclaw"
    staging = target_root + "/.migration-staging"
    target_system_python = remote_python(target)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    direct_context: dict[str, str] | None = None
    source_stopped = False
    hashes: dict[str, str] = {}
    total_bytes = 0
    try:
        print(
            json.dumps(
                {
                    "stage": "state_migration_gate",
                    "source_key_type": source_gate["key_type"],
                    "target_key_type": target_gate["key_type"],
                    "stable_second_connections": True,
                }
            ),
            flush=True,
        )
        prepare = f"""set -eu
umask 077
mkdir -p {q(target_root)} {q(staging)} {q(target_root + '/conda/envs')}
for item in epidemic-openclaw epidemic-dashboard-runtime epidemic-source-review-runtime epidemic-extractor-runtime; do
  if [ -e {q(target_root)}/\"$item\" ]; then
    mv {q(target_root)}/\"$item\" {q(target_root)}/\"$item.pre-migration-{timestamp}\"
  fi
done
for item in epidemic-core postgresql18; do
  if [ -e {q(target_root + '/conda/envs')}/\"$item\" ] && [ ! -f {q(target_root + '/conda/envs')}/\"$item/.epidemic-migration-ready\" ]; then
    mv {q(target_root + '/conda/envs')}/\"$item\" {q(target_root + '/conda/envs')}/\"$item.pre-migration-{timestamp}\"
  fi
done"""
        checked(target, prepare, timeout=120)
        checked(source, f"mkdir -p {q(source_tmp)} && chmod 700 {q(source_tmp)}")
        direct_context = setup_direct_transfer(source, target, target_creds)

        tools_dir = source_tmp + "/conda-pack-tools"
        core_python = source_envs + "/epidemic-core/bin/python"
        import_rc, _out, _err = run(
            source,
            f"PYTHONPATH={q(tools_dir)} {q(core_python)} -c 'import conda_pack'",
            timeout=30,
        )
        if import_rc:
            command = (
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"mkdir -p {q(tools_dir)}; "
                f"{q(core_python)} -m pip install --disable-pip-version-check --no-input "
                f"--timeout 60 --retries 2 --cache-dir {q(source_project + '/storage/cache/pip')} "
                f"--upgrade --target {q(tools_dir)} conda-pack==0.8.1 setuptools==80.9.0"
            )
            run_long(source, command, "install_conda_pack", 900)

        for env_name in ("epidemic-core", "postgresql18"):
            target_env = target_root + "/conda/envs/" + env_name
            marker_rc, _marker_out, _marker_err = run(
                target,
                f"test -f {q(target_env + '/.epidemic-migration-ready')}",
                timeout=30,
            )
            if marker_rc == 0:
                hashes[env_name] = "reused_verified_environment"
                print(
                    json.dumps({"stage": f"reuse_{env_name}", "status": "verified"}),
                    flush=True,
                )
                continue
            source_env = source_envs + "/" + env_name
            source_archive = source_tmp + f"/{env_name}.tar.gz"
            target_archive = staging + f"/{env_name}.tar.gz"
            pack_code = (
                "from conda_pack import pack;"
                f"pack(prefix={source_env!r},output={source_archive!r},force=True,"
                "ignore_missing_files=True,ignore_editable_packages=True)"
            )
            run_long(
                source,
                f"PYTHONPATH={q(tools_dir)} {q(core_python)} -c {q(pack_code)}",
                f"pack_{env_name}",
                3600,
            )
            source_hash = sha256(source, source_archive)
            total_bytes += direct_copy_file(
                source,
                source_archive,
                target,
                target_archive,
                direct_context,
                f"transfer_{env_name}",
                3600,
            )
            if sha256(target, target_archive) != source_hash:
                raise MigrationError(f"{env_name.upper()}_HASH_MISMATCH")
            hashes[env_name] = source_hash
            run_long(
                target,
                f"mkdir -p {q(target_env)} && tar -xzf {q(target_archive)} "
                f"-C {q(target_env)} --no-same-owner && "
                f"{q(target_system_python)} {q(target_env + '/bin/conda-unpack')}",
                f"extract_relocate_{env_name}",
                1800,
            )
            checked(target, f"touch {q(target_env + '/.epidemic-migration-ready')}")
            checked(source, f"rm -f {q(source_archive)}")
            checked(target, f"rm -f {q(target_archive)}")

        expected_wheel_hash = "597949743f2a00c0539d9e2ff0f67b32c608a378e973f99e7529fd6fa9445f70"
        find_code = r'''import hashlib,os,pathlib
expected=os.environ["EXPECTED"]
root=pathlib.Path("USER_HOME/autodl-tmp/epidemic-openclaw/storage/cache/pip")
for p in root.rglob("*"):
 if not p.is_file() or p.stat().st_size<400_000_000:continue
 h=hashlib.sha256()
 with p.open("rb") as f:
  for chunk in iter(lambda:f.read(8*1024*1024),b""):h.update(chunk)
 if h.hexdigest()==expected:print(p);break
'''
        wheel_source = checked(
            source,
            f"EXPECTED={q(expected_wheel_hash)} {q(core_python)} -c {q(find_code)}",
            timeout=1800,
        )
        if not wheel_source:
            wheel_source = source_tmp + "/vllm-0.24.0+cu129.whl"
            wheel_url = (
                "https://github.com/vllm-project/vllm/releases/download/v0.24.0/"
                "vllm-0.24.0%2Bcu129-cp38-abi3-manylinux_2_28_x86_64.whl"
            )
            turbo = source_project + "/scripts/download/with_autodl_turbo.sh"
            try:
                run_long(
                    source,
                    f"{q(turbo)} curl -fL --connect-timeout 30 --max-time 3600 "
                    f"--retry 2 --retry-delay 5 -C - -o {q(wheel_source)} {q(wheel_url)}",
                    "download_vllm_wheel_turbo",
                    4200,
                )
            except MigrationError:
                checked(source, "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY")
                run_long(
                    source,
                    f"curl -fL --connect-timeout 30 --max-time 3600 --retry 1 "
                    f"-C - -o {q(wheel_source)} {q(wheel_url)}",
                    "download_vllm_wheel_direct_fallback",
                    4200,
                )
        if sha256(source, wheel_source) != expected_wheel_hash:
            raise MigrationError("VLLM_WHEEL_SOURCE_HASH_MISMATCH")
        wheel_target = staging + "/vllm-0.24.0+cu129.whl"
        total_bytes += direct_copy_file(
            source,
            wheel_source,
            target,
            wheel_target,
            direct_context,
            "transfer_vllm_wheel",
            7200,
        )
        if sha256(target, wheel_target) != expected_wheel_hash:
            raise MigrationError("VLLM_WHEEL_TARGET_HASH_MISMATCH")
        hashes["vllm_wheel"] = expected_wheel_hash

        run_long(
            source,
            q(source_project + "/scripts/services/stop_all.sh"),
            "stop_source_for_state_snapshot",
            900,
        )
        source_stopped = True
        project_archive = source_tmp + "/epidemic-state.tar.gz"
        tar_command = (
            f"tar --exclude='epidemic-openclaw/models' "
            f"--exclude='epidemic-openclaw/runtime' "
            f"--exclude='epidemic-openclaw/storage/cache' "
            f"--exclude='epidemic-openclaw/storage/tmp' "
            f"--exclude='epidemic-openclaw/storage/run/*' "
            f"--exclude='epidemic-openclaw/integrations/openclaw/epidemic-query-tools/node_modules' "
            f"--exclude='epidemic-openclaw/.mypy_cache' "
            f"--exclude='epidemic-openclaw/.pytest_cache' "
            f"--exclude='epidemic-openclaw/.ruff_cache' "
            f"-czf {q(project_archive)} -C {q(source_root)} "
            "epidemic-openclaw epidemic-dashboard-runtime "
            "epidemic-source-review-runtime epidemic-extractor-runtime"
        )
        run_long(source, tar_command, "create_consistent_state_archive", 1800)
        run_long(
            source,
            q(source_project + "/scripts/services/start_all.sh"),
            "restore_source_services",
            1800,
        )
        source_stopped = False
        source_hash = sha256(source, project_archive)
        target_archive = staging + "/epidemic-state.tar.gz"
        total_bytes += direct_copy_file(
            source,
            project_archive,
            target,
            target_archive,
            direct_context,
            "transfer_state_snapshot",
            7200,
        )
        if sha256(target, target_archive) != source_hash:
            raise MigrationError("STATE_ARCHIVE_HASH_MISMATCH")
        hashes["state"] = source_hash
        run_long(
            target,
            f"tar -xzf {q(target_archive)} -C {q(target_root)} --no-same-owner",
            "extract_state_snapshot",
            1800,
        )
        checked(source, f"rm -f {q(project_archive)}")
        checked(target, f"rm -f {q(target_archive)}")
        checked(
            target,
            f"mkdir -p {q(target_project + '/models/llm')} "
            f"{q(target_project + '/runtime')} "
            f"{q(target_project + '/storage/cache/huggingface')} "
            f"{q(target_project + '/storage/cache/modelscope')} "
            f"{q(target_project + '/storage/cache/pip')} "
            f"{q(target_project + '/storage/cache/npm')} "
            f"{q(target_project + '/storage/cache/torch')} "
            f"{q(target_project + '/storage/cache/xdg')} "
            f"{q(target_project + '/storage/tmp')}",
        )
        relocation = relocate_target(target, target_root, target_project)
        checked(
            target,
            f"find {q(target_project + '/storage')} "
            "\\( -type f -o -type s \\) "
            "\\( -name '*.pid' -o -name '*.sock' -o -name ready_epoch -o -name start_epoch \\) -delete",
            timeout=300,
        )
        print(
            json.dumps(
                {
                    "stage": "state_migration_complete",
                    "transferred_bytes": total_bytes,
                    "archive_sha256": hashes,
                    "relocation": relocation,
                    "source_services_restored": True,
                    "vllm_wheel_staged": True,
                }
            ),
            flush=True,
        )
    finally:
        if source_stopped:
            try:
                run_long(
                    source,
                    q(source_project + "/scripts/services/start_all.sh"),
                    "emergency_restore_source_services",
                    1800,
                )
            except Exception:
                print(json.dumps({"stage": "source_restore", "status": "failed"}), flush=True)
        if direct_context is not None:
            try:
                cleanup_direct_transfer(source, target, direct_context)
            except Exception:
                print(json.dumps({"stage": "ephemeral_transfer_access", "status": "cleanup_failed"}), flush=True)
        source.close()
        target.close()


def resume_environment_relocation_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        core = root + "/conda/envs/epidemic-core"
        postgres = root + "/conda/envs/postgresql18"
        python = remote_python(target)
        checked(
            target,
            f"test -x {q(core + '/bin/python')} && "
            f"{q(core + '/bin/python')} -c 'import sys; assert sys.version_info[:2] == (3,10)'",
            timeout=60,
        )
        checked(
            target,
            f"test -f {q(postgres + '/bin/conda-unpack')} && "
            f"{q(python)} {q(postgres + '/bin/conda-unpack')} && "
            f"{q(postgres + '/bin/postgres')} --version >/dev/null",
            timeout=300,
        )
        checked(
            target,
            f"touch {q(core + '/.epidemic-migration-ready')} "
            f"{q(postgres + '/.epidemic-migration-ready')}",
        )
        versions = {
            "core_python": checked(target, f"{q(core + '/bin/python')} --version"),
            "postgresql": checked(target, f"{q(postgres + '/bin/postgres')} --version"),
        }
        print(json.dumps({"target_gate": gate, "environments": versions}, indent=2))
    finally:
        target.close()


def restore_source_services_action() -> None:
    creds = credentials(SOURCE_INFO)
    source, gate = connect_with_gate("source", creds)
    try:
        services = {
            "dashboard": ("scripts/dashboard/status_dashboard.sh", "scripts/dashboard/start_dashboard.sh"),
            "discovery_worker": (
                "scripts/source_discovery_worker/status_source_discovery_worker.sh",
                "scripts/source_discovery_worker/start_source_discovery_worker.sh",
            ),
            "promotion_worker": (
                "scripts/source_promotion_worker/status_source_promotion_worker.sh",
                "scripts/source_promotion_worker/start_source_promotion_worker.sh",
            ),
            "review_console": (
                "scripts/source_review/status_source_review.sh",
                "scripts/source_review/start_source_review.sh",
            ),
        }
        result: dict[str, bool] = {}
        for name, (status_rel, start_rel) in services.items():
            status_path = PROJECT + "/" + status_rel
            start_path = PROJECT + "/" + start_rel
            rc, _out, _err = run(source, q(status_path), timeout=60)
            if rc:
                run_long(source, q(start_path), f"restore_source_{name}", 600)
            result[name] = run(source, q(status_path), timeout=60)[0] == 0
        print(json.dumps({"source_gate": gate, "services": result}, indent=2))
    finally:
        source.close()


def resume_state_transfer_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    source.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    target.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    context: dict[str, str] | None = None
    try:
        source_archive = "USER_HOME/autodl-tmp/epidemic-migration-staging/epidemic-state.tar.gz"
        checked(source, f"test -s {q(source_archive)}")
        checked(source, q(PROJECT + "/scripts/services/status_all.sh"), timeout=300)
        source_hash = sha256(source, source_archive)
        home = checked(target, "printf '%s' \"$HOME\"")
        target_root = home.rstrip("/") + "/autodl-tmp"
        target_project = target_root + "/epidemic-openclaw"
        staging = target_root + "/.migration-staging"
        expected_wheel = "597949743f2a00c0539d9e2ff0f67b32c608a378e973f99e7529fd6fa9445f70"
        wheel_target = staging + "/vllm-0.24.0+cu129.whl"
        if sha256(target, wheel_target) != expected_wheel:
            raise MigrationError("STAGED_VLLM_WHEEL_HASH_MISMATCH")
        for env_name in ("epidemic-core", "postgresql18"):
            checked(
                target,
                f"test -f {q(target_root + '/conda/envs/' + env_name + '/.epidemic-migration-ready')}",
            )
        if run(target, f"test -e {q(target_project)}", timeout=30)[0] == 0:
            backup = target_project + ".pre-state-resume-" + time.strftime(
                "%Y%m%dT%H%M%SZ", time.gmtime()
            )
            checked(target, f"mv {q(target_project)} {q(backup)}")
        context = setup_direct_transfer(source, target, target_creds)
        target_archive = staging + "/epidemic-state.tar.gz"
        transferred = direct_copy_file(
            source,
            source_archive,
            target,
            target_archive,
            context,
            "transfer_state_snapshot",
            7200,
        )
        if sha256(target, target_archive) != source_hash:
            raise MigrationError("STATE_ARCHIVE_HASH_MISMATCH")
        run_long(
            target,
            f"tar -xzf {q(target_archive)} -C {q(target_root)} --no-same-owner",
            "extract_state_snapshot",
            1800,
        )
        checked(
            target,
            f"mkdir -p {q(target_project + '/models/llm')} "
            f"{q(target_project + '/runtime')} "
            f"{q(target_project + '/storage/cache/huggingface')} "
            f"{q(target_project + '/storage/cache/modelscope')} "
            f"{q(target_project + '/storage/cache/pip')} "
            f"{q(target_project + '/storage/cache/npm')} "
            f"{q(target_project + '/storage/cache/torch')} "
            f"{q(target_project + '/storage/cache/xdg')} "
            f"{q(target_project + '/storage/tmp')}",
        )
        relocation = relocate_target(target, target_root, target_project)
        checked(
            target,
            f"find {q(target_project + '/storage')} "
            "\\( -type f -o -type s \\) "
            "\\( -name '*.pid' -o -name '*.sock' -o -name ready_epoch -o -name start_epoch \\) -delete",
            timeout=300,
        )
        checked(source, f"rm -f {q(source_archive)}")
        checked(target, f"rm -f {q(target_archive)}")
        print(
            json.dumps(
                {
                    "stage": "state_transfer_complete",
                    "source_gate": source_gate,
                    "target_gate": target_gate,
                    "bytes": transferred,
                    "sha256": source_hash,
                    "relocation": relocation,
                    "source_services_healthy": True,
                }
            ),
            flush=True,
        )
    finally:
        if context is not None:
            try:
                cleanup_direct_transfer(source, target, context)
            except Exception:
                print(json.dumps({"stage": "ephemeral_transfer_access", "status": "cleanup_failed"}), flush=True)
        source.close()
        target.close()


def rebuild_target_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    target.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        staging = root + "/.migration-staging"
        qwen_env = root + "/conda/envs/qwen-vllm"
        wheel = staging + "/vllm-0.24.0+cu129-cp38-abi3-manylinux_2_28_x86_64.whl"
        legacy_wheel = staging + "/vllm-0.24.0+cu129.whl"
        expected_wheel = "597949743f2a00c0539d9e2ff0f67b32c608a378e973f99e7529fd6fa9445f70"
        checked(target, f"test -d {q(project + '/.git')} && test -f {q(project + '/config/qwen-vllm-pip-freeze.txt')}")
        if run(target, f"test -f {q(wheel)}", timeout=30)[0] != 0:
            checked(target, f"test -f {q(legacy_wheel)} && mv {q(legacy_wheel)} {q(wheel)}")
        if sha256(target, wheel) != expected_wheel:
            raise MigrationError("STAGED_VLLM_WHEEL_HASH_MISMATCH")
        source_pip = checked(
            source,
            "USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python -m pip --version | awk '{print $2}'",
        )
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", source_pip):
            raise MigrationError("SOURCE_PIP_VERSION_INVALID")

        runtime_marker = qwen_env + "/.epidemic-runtime-ready"
        if run(target, f"test -f {q(runtime_marker)}", timeout=30)[0] != 0:
            if run(target, f"test -e {q(qwen_env)}", timeout=30)[0] == 0:
                backup = qwen_env + ".incomplete-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                checked(target, f"mv {q(qwen_env)} {q(backup)}")
            checked(target, f"python3 -m venv --without-pip {q(qwen_env)}", timeout=120)
            core_python = root + "/conda/envs/epidemic-core/bin/python"
            run_long(
                target,
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"PIP_CACHE_DIR={q(project + '/storage/cache/pip')} "
                f"{q(core_python)} -m pip --python {q(qwen_env + '/bin/python')} install "
                f"--disable-pip-version-check --no-input --timeout 120 --retries 3 {q('pip==' + source_pip)}",
                "bootstrap_target_pip",
                900,
            )
            filter_code = r'''import pathlib,os
source=pathlib.Path(os.environ["SOURCE"])
target=pathlib.Path(os.environ["TARGET"])
lines=[]
for line in source.read_text().splitlines():
 if line.lstrip().startswith("vllm @ "):continue
 lines.append(line)
target.write_text("\n".join(lines)+"\n")
'''
            filtered = staging + "/qwen-vllm-without-vllm.lock"
            target_python = remote_python(target)
            checked(
                target,
                f"SOURCE={q(project + '/config/qwen-vllm-pip-freeze.txt')} "
                f"TARGET={q(filtered)} {q(target_python)} -c {q(filter_code)}",
            )
            install_prefix = (
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"PIP_CACHE_DIR={q(project + '/storage/cache/pip')} "
                f"{q(qwen_env + '/bin/python')} -m pip install --disable-pip-version-check "
                "--no-input --timeout 120 --retries 3 "
            )
            run_long(
                target,
                install_prefix + f"{q(wheel)}",
                "install_locked_vllm_and_cuda_dependencies",
                7200,
            )
            run_long(
                target,
                install_prefix + f"-r {q(filtered)}",
                "apply_locked_qwen_environment",
                7200,
            )
            checked(
                target,
                f"{q(qwen_env + '/bin/python')} -m pip check && "
                f"{q(qwen_env + '/bin/python')} -c "
                + q(
                    "import torch,vllm;"
                    "assert torch.__version__=='2.11.0+cu129';"
                    "assert vllm.__version__=='0.24.0+cu129';"
                    "assert torch.version.cuda=='12.9'"
                ),
                timeout=600,
            )
            checked(target, f"touch {q(runtime_marker)}")
            print(json.dumps({"stage": "qwen_runtime_rebuild", "status": "complete"}), flush=True)
        else:
            print(json.dumps({"stage": "qwen_runtime_rebuild", "status": "reused"}), flush=True)

        model_dir = project + "/models/llm/Qwen3-8B-AWQ"
        verify_code = r'''import hashlib,json,os,pathlib,sys
root=pathlib.Path(os.environ["MODEL"])
manifest=json.loads(pathlib.Path(os.environ["MANIFEST"]).read_text())
rows=manifest.get("files",[]);checked=0;total=0
for row in rows:
 rel=row.get("path") or row.get("name") or row.get("file")
 expected=row.get("sha256")
 if not rel or not expected:continue
 p=root/rel
 if not p.is_file():sys.exit(2)
 h=hashlib.sha256()
 with p.open("rb") as f:
  for chunk in iter(lambda:f.read(8*1024*1024),b""):h.update(chunk)
 if h.hexdigest()!=expected:sys.exit(3)
 checked+=1;total+=p.stat().st_size
if checked!=13:sys.exit(4)
print(json.dumps({"files":checked,"bytes":total}))
'''
        manifest = project + "/config/qwen3-8b-awq-model-manifest.json"
        model_env = (
            f"MODEL={q(model_dir)} MANIFEST={q(manifest)} "
            f"{q(qwen_env + '/bin/python')} -c {q(verify_code)}"
        )
        model_rc, model_out, _model_err = run(target, model_env, timeout=1800)
        if model_rc != 0:
            checked(target, f"mkdir -p {q(model_dir)}")
            download_code = (
                "from modelscope.hub.snapshot_download import snapshot_download;"
                f"snapshot_download('Qwen/Qwen3-8B-AWQ',revision='e6d5e9ba92fd11fe236428b6e27ff0277f6e6c4d',local_dir={model_dir!r})"
            )
            run_long(
                target,
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"MODELSCOPE_CACHE={q(project + '/storage/cache/modelscope')} "
                f"HF_HOME={q(project + '/storage/cache/huggingface')} "
                f"{q(qwen_env + '/bin/python')} -c {q(download_code)}",
                "download_qwen_model_modelscope",
                10800,
            )
            model_out = checked(target, model_env, timeout=3600)
        model_result = json.loads(model_out)
        print(
            json.dumps({"stage": "qwen_model_verification", "status": "complete", **model_result}),
            flush=True,
        )

        node_home = project + "/runtime/node-v24.18.0-linux-x64"
        node_current = project + "/runtime/node-current"
        openclaw_home = project + "/runtime/openclaw-npm"
        node_marker = project + "/runtime/.node-openclaw-ready"
        if run(target, f"test -f {q(node_marker)}", timeout=30)[0] != 0:
            node_tar = staging + "/node-v24.18.0-linux-x64.tar.xz"
            node_url = "https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz"
            run_long(
                target,
                f"curl -fL --connect-timeout 30 --max-time 1800 --retry 2 --retry-delay 5 "
                f"-o {q(node_tar)} {q(node_url)}",
                "download_node_official",
                2100,
            )
            expected_node = "55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742"
            if sha256(target, node_tar) != expected_node:
                raise MigrationError("NODE_ARCHIVE_HASH_MISMATCH")
            checked(
                target,
                f"mkdir -p {q(project + '/runtime')} && tar -xJf {q(node_tar)} "
                f"-C {q(project + '/runtime')} && ln -sfn {q(node_home)} {q(node_current)}",
                timeout=600,
            )
            npm = node_current + "/bin/npm"
            node = node_current + "/bin/node"
            expected_integrity = "sha512-T+P/g19IheeT1ckXMoPN61dYuE8vBF4MderI+kWkvpuFYxPkJxn8AXLpu9IXCnN9g36Acpm9+mMD/V+lsvOkyA=="
            actual_integrity = checked(
                target,
                f"PATH={q(node_current + '/bin')}:\"$PATH\" "
                f"NPM_CONFIG_CACHE={q(project + '/storage/cache/npm')} {q(npm)} view "
                f"openclaw@2026.6.11 dist.integrity --registry=https://registry.npmjs.org",
                timeout=300,
            )
            if actual_integrity.strip() != expected_integrity:
                raise MigrationError("OPENCLAW_NPM_INTEGRITY_MISMATCH")
            run_long(
                target,
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"PATH={q(node_current + '/bin')}:\"$PATH\" "
                f"NPM_CONFIG_CACHE={q(project + '/storage/cache/npm')} {q(npm)} install "
                f"--prefix {q(openclaw_home)} --omit=dev --no-audit --no-fund "
                "--registry=https://registry.npmjs.org openclaw@2026.6.11",
                "install_openclaw_npm",
                3600,
            )
            integration = project + "/integrations/openclaw/epidemic-query-tools"
            run_long(
                target,
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"cd {q(integration)} && NPM_CONFIG_CACHE={q(project + '/storage/cache/npm')} "
                f"PATH={q(node_current + '/bin')}:\"$PATH\" "
                f"{q(npm)} ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org",
                "install_openclaw_integration_lockfile",
                3600,
            )
            checked(
                target,
                f"{q(node)} --version | grep -Fx 'v24.18.0' >/dev/null && "
                f"PATH={q(node_current + '/bin')}:\"$PATH\" "
                f"{q(openclaw_home + '/node_modules/.bin/openclaw')} --version | grep -F '2026.6.11' >/dev/null && "
                f"touch {q(node_marker)}",
                timeout=300,
            )
            print(json.dumps({"stage": "node_openclaw_rebuild", "status": "complete"}), flush=True)
        else:
            print(json.dumps({"stage": "node_openclaw_rebuild", "status": "reused"}), flush=True)

        relocation = relocate_target(target, root, project)
        proxy_state = checked(
            target,
            "bash -lc '[[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]] && echo clean || echo present'",
        )
        print(
            json.dumps(
                {
                    "stage": "target_rebuild_complete",
                    "source_gate": source_gate,
                    "target_gate": target_gate,
                    "relocation": relocation,
                    "proxy_state": proxy_state,
                }
            ),
            flush=True,
        )
    finally:
        source.close()
        target.close()


def target_pip_diagnose_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        envs = root + "/conda/envs"
        candidates = checked(
            target,
            f"find {q(envs)} -maxdepth 1 -type d -name 'qwen-vllm*' -printf '%f\\n' | sort",
        ).splitlines()
        active = next((name for name in candidates if name == "qwen-vllm"), "")
        if not active:
            raise MigrationError("TARGET_QWEN_ENV_NOT_FOUND")
        python = envs + "/" + active + "/bin/python"
        wheel = root + "/.migration-staging/vllm-0.24.0+cu129.whl"
        diag = r'''import json,os,pathlib,re,subprocess,zipfile
wheel=pathlib.Path(os.environ["WHEEL"])
lock=pathlib.Path(os.environ["LOCK"])
requires=[]
with zipfile.ZipFile(wheel) as z:
 name=next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
 text=z.read(name).decode("utf-8","replace")
 for line in text.splitlines():
  if line.startswith("Requires-Dist:") and re.search(r"torch|nvidia|flashinfer|cuda",line,re.I):requires.append(line)
direct=[]
for line in lock.read_text().splitlines():
 if " @ " in line:
  direct.append(re.sub(r"https?://[^\s]+","[url]",line))
cmd=[os.environ["PYTHON"],"-m","pip","install","--dry-run","--disable-pip-version-check","--no-input","--timeout","60","--retries","1","-r",os.environ["FILTERED"]]
p=subprocess.run(cmd,text=True,capture_output=True,timeout=600)
combined=(p.stdout+"\n"+p.stderr).splitlines()
errors=[]
for line in combined:
 if re.search(r"error|could not find|no matching|conflict|resolutionimpossible|requires-python",line,re.I):
  line=re.sub(r"https?://[^\s]+","[url]",line)
  line=re.sub(r"(?i)(token|password|secret)=\S+",r"\1=[redacted]",line)
  errors.append(line[-500:])
installed=json.loads(subprocess.run([os.environ["PYTHON"],"-m","pip","list","--format=json"],text=True,capture_output=True).stdout)
print(json.dumps({"wheel_requires":requires,"direct_lock_entries":direct,"dry_run_rc":p.returncode,"errors":errors[-20:],"installed_count":len(installed)}))
'''
        filtered = root + "/.migration-staging/qwen-vllm-without-vllm.lock"
        result = json.loads(
            checked(
                target,
                f"WHEEL={q(wheel)} LOCK={q(project + '/config/qwen-vllm-pip-freeze.txt')} "
                f"FILTERED={q(filtered)} PYTHON={q(python)} {q(python)} -c {q(diag)}",
                timeout=900,
            )
        )
        print(json.dumps({"target_gate": gate, "diagnosis": result}, indent=2))
    finally:
        target.close()


def finish_qwen_runtime_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        env = root + "/conda/envs/qwen-vllm"
        staging = root + "/.migration-staging"
        python = env + "/bin/python"
        checked(
            target,
            f"{q(python)} -c "
            + q(
                "import torch,vllm;"
                "assert vllm.__version__=='0.24.0'"
            ),
            timeout=300,
        )
        cuda_check = (
            f"{q(python)} -c "
            + q(
                "import torch;"
                "assert torch.__version__=='2.11.0+cu129';"
                "assert torch.version.cuda=='12.9'"
            )
        )
        if run(target, cuda_check, timeout=300)[0] != 0:
            run_long(
                target,
                "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                f"PIP_CACHE_DIR={q(project + '/storage/cache/pip')} {q(python)} -m pip install "
                "--disable-pip-version-check --no-input --timeout 120 --retries 3 "
                "--upgrade --force-reinstall --index-url https://download.pytorch.org/whl/cu129 "
                "torch==2.11.0+cu129 torchvision==0.26.0+cu129 torchaudio==2.11.0+cu129",
                "install_official_pytorch_cu129",
                7200,
            )
        else:
            print(
                json.dumps({"stage": "install_official_pytorch_cu129", "status": "reused_verified"}),
                flush=True,
            )
        filter_code = r'''import pathlib,os,re
source=pathlib.Path(os.environ["SOURCE"])
target=pathlib.Path(os.environ["TARGET"])
skip=("vllm @ ","torch==","torchaudio==","torchvision==")
lines=[line for line in source.read_text().splitlines() if not line.lstrip().startswith(skip)]
target.write_text("\n".join(lines)+"\n")
'''
        filtered = staging + "/qwen-vllm-wheel-owned-excluded.lock"
        system_python = remote_python(target)
        checked(
            target,
            f"SOURCE={q(project + '/config/qwen-vllm-pip-freeze.txt')} TARGET={q(filtered)} "
            f"{q(system_python)} -c {q(filter_code)}",
        )
        run_long(
            target,
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PIP_CACHE_DIR={q(project + '/storage/cache/pip')} {q(python)} -m pip install "
            f"--disable-pip-version-check --no-input --timeout 120 --retries 3 -r {q(filtered)}",
            "apply_remaining_locked_qwen_environment",
            7200,
        )
        checked(
            target,
            f"{q(python)} -m pip check && {q(python)} -c "
            + q(
                "import importlib.metadata,torch,vllm;"
                "assert torch.__version__=='2.11.0+cu129';"
                "assert vllm.__version__=='0.24.0';"
                "assert importlib.metadata.version('vllm')=='0.24.0+cu129';"
                "assert torch.version.cuda=='12.9';"
                "assert torch.cuda.is_available()"
            )
            + f" && touch {q(env + '/.epidemic-runtime-ready')}",
            timeout=600,
        )
        print(
            json.dumps(
                {
                    "target_gate": gate,
                    "runtime": "vllm 0.24.0+cu129",
                    "torch": "2.11.0+cu129",
                    "cuda": "12.9",
                    "pip_check": "pass",
                },
                indent=2,
            )
        )
    finally:
        target.close()


def target_qwen_versions_action() -> None:
    target_creds = credentials(TARGET_INFO)
    source_creds = credentials(SOURCE_INFO)
    target, gate = connect_with_gate("target", target_creds)
    source, source_gate = connect_with_gate("source", source_creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        python = home.rstrip("/") + "/autodl-tmp/conda/envs/qwen-vllm/bin/python"
        code = "import json,torch,vllm;print(json.dumps({'torch':torch.__version__,'vllm':vllm.__version__,'cuda':torch.version.cuda}))"
        versions = json.loads(checked(target, f"{q(python)} -c {q(code)}", timeout=300))
        source_python = "USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python"
        source_versions = json.loads(
            checked(source, f"{q(source_python)} -c {q(code)}", timeout=300)
        )
        print(
            json.dumps(
                {
                    "target_gate": gate,
                    "source_gate": source_gate,
                    "target_versions": versions,
                    "source_versions": source_versions,
                },
                indent=2,
            )
        )
    finally:
        target.close()
        source.close()


def target_install_progress_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        script = r'''import json,os,pathlib,subprocess
root=pathlib.Path(os.environ["ROOT"])
def size(path):
 try:return int(subprocess.run(["du","-sb",str(path)],text=True,capture_output=True,timeout=120).stdout.split()[0])
 except Exception:return 0
p=subprocess.run(["ps","-eo","etimes=,pcpu=,pmem=,args="],text=True,capture_output=True).stdout.splitlines()
rows=[]
for line in p:
 if str(root/"conda/envs/qwen-vllm/bin/python") in line:
  cols=line.strip().split(None,3);rows.append({"elapsed_seconds":int(cols[0]),"cpu_percent":float(cols[1]),"memory_percent":float(cols[2])})
print(json.dumps({"qwen_environment_bytes":size(root/"conda/envs/qwen-vllm"),"pip_cache_bytes":size(root/"epidemic-openclaw/storage/cache/pip"),"model_bytes":size(root/"epidemic-openclaw/models/llm/Qwen3-8B-AWQ"),"matching_install_processes":rows}))
'''
        python = remote_python(target)
        result = json.loads(
            checked(target, f"ROOT={q(root)} {q(python)} -c {q(script)}", timeout=300)
        )
        print(json.dumps({"target_gate": gate, "progress": result}, indent=2))
    finally:
        target.close()


def start_target_services_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    target.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        services = (
            ("postgres", "scripts/postgres/status.sh", "scripts/postgres/start.sh", 600),
            ("vllm", "scripts/qwen_vllm/status.sh", "scripts/qwen_vllm/start.sh", 1800),
            ("query_api", "scripts/query_api/status_query_api.sh", "scripts/query_api/start_query_api.sh", 600),
            ("retrieval_worker", "scripts/retrieval_worker/status_retrieval_worker.sh", "scripts/retrieval_worker/start_retrieval_worker.sh", 600),
            ("openclaw", "scripts/openclaw/status_openclaw.sh", "scripts/openclaw/start_openclaw.sh", 900),
            ("dashboard", "scripts/dashboard/status_dashboard.sh", "scripts/dashboard/start_dashboard.sh", 600),
            ("discovery_worker", "scripts/source_discovery_worker/status_source_discovery_worker.sh", "scripts/source_discovery_worker/start_source_discovery_worker.sh", 600),
            ("promotion_worker", "scripts/source_promotion_worker/status_source_promotion_worker.sh", "scripts/source_promotion_worker/start_source_promotion_worker.sh", 600),
            ("review_console", "scripts/source_review/status_source_review.sh", "scripts/source_review/start_source_review.sh", 600),
            ("daily_update", "scripts/daily_update/status_daily_update.sh", "scripts/daily_update/start_daily_update.sh", 600),
            ("preview_worker", "scripts/preview_publication_worker/status_preview_publication_worker.sh", "scripts/preview_publication_worker/start_preview_publication_worker.sh", 600),
        )
        result: dict[str, bool] = {}
        for name, status_rel, start_rel, timeout in services:
            status = project + "/" + status_rel
            start = project + "/" + start_rel
            if run(target, q(status), timeout=120)[0] != 0:
                command = (
                    "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
                    + q(start)
                )
                run_long(target, command, f"start_target_{name}", timeout)
            healthy = run(target, q(status), timeout=180)[0] == 0
            result[name] = healthy
            if not healthy:
                raise MigrationError(f"TARGET_{name.upper()}_HEALTH_FAILED")
        print(json.dumps({"target_gate": gate, "services": result}, indent=2))
    finally:
        target.close()


def reconcile_target_daily_startup_action() -> None:
    """Run the deterministic startup catch-up on the current A100 only."""
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        required = (
            "scripts/postgres/status.sh",
            "scripts/openclaw/status_openclaw.sh",
            "scripts/daily_update/status_daily_update.sh",
            "scripts/daily_update/reconcile_due.sh",
        )
        for relative in required[:-1]:
            if run(target, q(project + "/" + relative), timeout=180)[0] != 0:
                raise MigrationError("TARGET_STARTUP_RECONCILE_SERVICE_NOT_HEALTHY")
        command = (
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            + q(project + "/scripts/daily_update/reconcile_due.sh")
            + " --trigger startup"
        )
        rc, output, error = run(target, command, timeout=300)
        if rc:
            raise MigrationError("TARGET_STARTUP_RECONCILE_FAILED")
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise MigrationError("TARGET_STARTUP_RECONCILE_INVALID_OUTPUT") from exc
        if not isinstance(value, dict) or value.get("status") != "PASS" or value.get("trigger") != "startup":
            raise MigrationError("TARGET_STARTUP_RECONCILE_INVALID_RESULT")
        created = value.get("created")
        deduplicated = value.get("deduplicated")
        run_ids = value.get("run_ids")
        if not isinstance(created, int) or not isinstance(deduplicated, int) or not isinstance(run_ids, list):
            raise MigrationError("TARGET_STARTUP_RECONCILE_INVALID_RESULT")
        print(
            json.dumps(
                {
                    "target_gate": {
                        "key_type": gate["key_type"],
                        "stable_second_connection": gate["stable_second_connection"],
                    },
                    "startup_reconcile": {
                        "status": "PASS",
                        "created": created,
                        "deduplicated": deduplicated,
                        "run_count": len(run_ids),
                    },
                    "source_server_contacted": False,
                },
                indent=2,
            )
        )
    finally:
        target.close()


def diagnose_target_postgres_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        root = home.rstrip("/") + "/autodl-tmp"
        script = r'''import json,os,pathlib,re,socket,subprocess
home=os.environ["HOME_PATH"];project=pathlib.Path(os.environ["PROJECT"]);root=pathlib.Path(os.environ["ROOT_PATH"])
data=project/"storage/database/postgresql18/data";logs=project/"storage/database/postgresql18/logs";env=root/"conda/envs/postgresql18"
def run(args):
 try:
  p=subprocess.run(args,text=True,capture_output=True,timeout=60)
  return p.returncode,p.stdout.strip(),p.stderr.strip()
 except Exception as e:return 99,"",type(e).__name__
control=run([str(env/"bin/pg_controldata"),str(data)])
ldd=run(["ldd",str(env/"bin/postgres")])
old=[]
for p in data.rglob("*"):
 if not p.is_file() or p.stat().st_size>5_000_000:continue
 try:
  text=p.read_text()
  if "USER_HOME/autodl-tmp" in text:old.append(str(p.relative_to(data)))
 except Exception:pass
lines=[]
for p in sorted(logs.glob("*"),key=lambda x:x.stat().st_mtime if x.is_file() else 0)[-3:]:
 if not p.is_file():continue
 try:content=p.read_text(errors="replace").splitlines()[-30:]
 except Exception:continue
 for line in content:
  line=line.replace(home,"[target-home]")
  line=re.sub(r"(?i)(password|token|secret)=\S+",r"\1=[redacted]",line)
  lines.append(line[-500:])
s=socket.socket()
try:s.bind(("127.0.0.1",5432));port_free=True
except OSError:port_free=False
finally:s.close()
print(json.dumps({"data_exists":data.is_dir(),"data_mode":oct(data.stat().st_mode&0o777) if data.exists() else None,"postmaster_pid":(data/"postmaster.pid").exists(),"control_rc":control[0],"control_summary":[x for x in control[1].splitlines() if "Database cluster state" in x or "pg_control version" in x],"missing_libraries":[x.strip() for x in ldd[1].splitlines() if "not found" in x],"old_prefix_files":old,"port_free":port_free,"log_tail":lines[-40:]}))
'''
        python = remote_python(target)
        result = json.loads(
            checked(
                target,
                f"HOME_PATH={q(home)} PROJECT={q(project)} ROOT_PATH={q(root)} {q(python)} -c {q(script)}",
                timeout=300,
            )
        )
        print(json.dumps({"target_gate": gate, "postgres": result}, indent=2))
    finally:
        target.close()


def attempt_target_postgres_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        rc, output, error = run(
            target,
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            + q(project + "/scripts/postgres/start.sh"),
            timeout=600,
        )
        combined = (output + "\n" + error).splitlines()
        safe = []
        for line in combined:
            line = line.replace(home, "[target-home]")
            line = re.sub(r"(?i)(password|token|secret)=\S+", r"\1=[redacted]", line)
            safe.append(line[-500:])
        smoke_rc, smoke_out, smoke_err = run(
            target,
            q(project + "/scripts/postgres/smoke_test.sh"),
            timeout=300,
        )
        smoke_lines = []
        for line in (smoke_out + "\n" + smoke_err).splitlines():
            line = line.replace(home, "[target-home]")
            line = re.sub(r"(?i)(password|token|secret)=\S+", r"\1=[redacted]", line)
            smoke_lines.append(line[-500:])
        status = run(target, q(project + "/scripts/postgres/status.sh"), timeout=120)[0] == 0
        print(
            json.dumps(
                {
                    "target_gate": gate,
                    "start_rc": rc,
                    "start_output": safe[-30:],
                    "smoke_rc": smoke_rc,
                    "smoke_output": smoke_lines[-30:],
                    "status_after": status,
                },
                indent=2,
            )
        )
    finally:
        target.close()


def target_postgres_scripts_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        files = (
            "scripts/postgres/_common.sh",
            "scripts/postgres/start.sh",
            "scripts/postgres/smoke_test.sh",
            "scripts/postgres/status.sh",
            "scripts/postgres/stop.sh",
        )
        result: dict[str, list[str]] = {}
        for rel in files:
            text_value = checked(target, f"sed -n '1,240p' {q(project + '/' + rel)}")
            lines = []
            for line in text_value.splitlines():
                line = line.replace(home, "[target-home]")
                if re.search(r"(?i)(password|token|secret).*=", line):
                    line = re.sub(r"=(.*)$", "=[redacted]", line)
                lines.append(line)
            result[rel] = lines
        print(json.dumps({"target_gate": gate, "scripts": result}, indent=2))
    finally:
        target.close()


def patch_target_postgres_user_mode_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        script = r'''import json,os,pathlib
project=pathlib.Path(os.environ["PROJECT"]);root=pathlib.Path(os.environ["ROOT_PATH"])
common=project/"scripts/postgres/_common.sh";smoke=project/"scripts/postgres/smoke_test.sh"
common_text=common.read_text();old='run_pg() { runuser -u epidemicdb -- "$@"; }';new='run_pg() { "$@"; }'
if old not in common_text and new not in common_text:raise SystemExit(2)
common.write_text(common_text.replace(old,new))
smoke_text=smoke.read_text();smoke.write_text(smoke_text.replace("USER_HOME/miniconda3/bin/python",str(root/"conda/envs/epidemic-core/bin/python")))
common.chmod(0o755);smoke.chmod(0o755)
print(json.dumps({"runuser_removed":old not in common.read_text(),"smoke_python_relocated":"USER_HOME/miniconda3/bin/python" not in smoke.read_text()}))
'''
        python = remote_python(target)
        result = json.loads(
            checked(
                target,
                f"PROJECT={q(project)} ROOT_PATH={q(root)} {q(python)} -c {q(script)}",
            )
        )
        print(json.dumps({"target_gate": gate, "patch": result}, indent=2))
    finally:
        target.close()


def diagnose_target_vllm_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        files = ("scripts/qwen_vllm/_common.sh", "scripts/qwen_vllm/start.sh", "scripts/qwen_vllm/status.sh")
        scripts: dict[str, list[str]] = {}
        for rel in files:
            value = checked(target, f"sed -n '1,260p' {q(project + '/' + rel)}")
            scripts[rel] = [line.replace(home, "[target-home]") for line in value.splitlines()]
        rc, output, error = run(
            target,
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            + q(project + "/scripts/qwen_vllm/start.sh"),
            timeout=600,
        )
        safe_output = []
        for line in (output + "\n" + error).splitlines():
            line = line.replace(home, "[target-home]")
            line = re.sub(r"(?i)(password|token|secret)=\S+", r"\1=[redacted]", line)
            safe_output.append(line[-500:])
        python = remote_python(target)
        inspect_code = r'''import json,os,pathlib
root=pathlib.Path(os.environ["ROOT_PATH"]);project=pathlib.Path(os.environ["PROJECT"])
paths={"python":root/"conda/envs/qwen-vllm/bin/python","vllm":root/"conda/envs/qwen-vllm/bin/vllm","model":project/"models/llm/Qwen3-8B-AWQ"}
logs=project/"storage/logs/qwen-vllm";tail=[]
for p in sorted(logs.glob("*"),key=lambda x:x.stat().st_mtime if x.is_file() else 0)[-3:]:
 if p.is_file():
  try:tail.extend(p.read_text(errors="replace").splitlines()[-30:])
  except Exception:pass
print(json.dumps({"paths":{k:{"exists":p.exists(),"executable":os.access(p,os.X_OK),"mode":oct(p.stat().st_mode&0o777) if p.exists() else None} for k,p in paths.items()},"log_tail":tail[-40:]}))
'''
        detail = json.loads(
            checked(target, f"ROOT_PATH={q(root)} PROJECT={q(project)} {q(python)} -c {q(inspect_code)}")
        )
        detail["log_tail"] = [line.replace(home, "[target-home]")[-500:] for line in detail["log_tail"]]
        print(json.dumps({"target_gate": gate, "scripts": scripts, "start_rc": rc, "start_output": safe_output[-30:], "detail": detail}, indent=2))
    finally:
        target.close()


def patch_target_legacy_python_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        script = r'''import json,os,pathlib
scripts=pathlib.Path(os.environ["PROJECT"])/"scripts";new=str(pathlib.Path(os.environ["ROOT_PATH"])/"conda/envs/epidemic-core/bin/python")
old="USER_HOME/miniconda3/bin/python";changed=[]
for p in scripts.rglob("*"):
 if not p.is_file():continue
 try:text=p.read_text()
 except UnicodeError:continue
 if old in text:
  p.write_text(text.replace(old,new));changed.append(str(p.relative_to(scripts)))
print(json.dumps({"changed_files":changed,"remaining":sum(1 for p in scripts.rglob("*") if p.is_file() and old in p.read_text(errors="ignore"))}))
'''
        python = remote_python(target)
        result = json.loads(
            checked(target, f"PROJECT={q(project)} ROOT_PATH={q(root)} {q(python)} -c {q(script)}")
        )
        print(json.dumps({"target_gate": gate, "patch": result}, indent=2))
    finally:
        target.close()


def target_vllm_root_cause_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        python = root + "/conda/envs/qwen-vllm/bin/python"
        script = r'''import importlib.metadata,json,os,pathlib,re,torch
log=pathlib.Path(os.environ["LOG"]);home=os.environ["HOME_PATH"]
pattern=re.compile(r"error|exception|traceback|cuda|cublas|flash|awq|compute|capability|kernel|unsupported|failed|assert",re.I)
selected=[]
for line in log.read_text(errors="replace").splitlines():
 if pattern.search(line):selected.append(line.replace(home,"[target-home]")[-700:])
packages={}
for name in ("vllm","torch","flashinfer-python","flashinfer-cubin","compressed-tensors","transformers"):
 try:packages[name]=importlib.metadata.version(name)
 except Exception:packages[name]="missing"
print(json.dumps({"gpu_name":torch.cuda.get_device_name(0),"gpu_capability":torch.cuda.get_device_capability(0),"packages":packages,"matched_lines":selected[-240:]}))
'''
        result = json.loads(
            checked(
                target,
                f"LOG={q(project + '/storage/logs/qwen-vllm/vllm-server.log')} HOME_PATH={q(home)} "
                f"{q(python)} -c {q(script)}",
                timeout=600,
            )
        )
        print(json.dumps({"target_gate": gate, "root_cause": result}, indent=2))
    finally:
        target.close()


def target_gpu_status_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        raw = checked(
            target,
            "nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu "
            "--format=csv,noheader,nounits",
        )
        gpus = []
        for line in raw.splitlines():
            values = [part.strip() for part in line.split(",")]
            if len(values) == 6:
                gpus.append(
                    {
                        "index": int(values[0]),
                        "name": values[1],
                        "memory_total_mib": int(values[2]),
                        "memory_used_mib": int(values[3]),
                        "memory_free_mib": int(values[4]),
                        "utilization_percent": int(values[5]),
                    }
                )
        print(json.dumps({"target_gate": gate, "gpus": gpus}, indent=2))
    finally:
        target.close()


def patch_target_vllm_capacity_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        python = remote_python(target)
        script = r'''import json,os,pathlib
project=pathlib.Path(os.environ["PROJECT"])
common=project/"scripts/qwen_vllm/_common.sh"
start=project/"scripts/qwen_vllm/start.sh"
common_text=common.read_text()
start_text=start.read_text()
device_variants=(
 ("export CUDA_VISIBLE_DEVICES=0","export CUDA_VISIBLE_DEVICES=3"),
 ('export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"','export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-3}"'),
 ('CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"','CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-3}"'),
 ("CUDA_VISIBLE_DEVICES=0","CUDA_VISIBLE_DEVICES=3"),
)
old_util="--gpu-memory-utilization 0.85"
new_util="--gpu-memory-utilization 0.20"
device_ready=False
for old_device,new_device in device_variants:
 if old_device in common_text:
  common_text=common_text.replace(old_device,new_device);device_ready=True;break
 if new_device in common_text:device_ready=True;break
if not device_ready:raise SystemExit("unexpected CUDA_VISIBLE_DEVICES configuration")
if old_util in start_text:
 start_text=start_text.replace(old_util,new_util)
elif new_util not in start_text:
 raise SystemExit("unexpected gpu-memory-utilization configuration")
common.write_text(common_text)
start.write_text(start_text)
print(json.dumps({"cuda_visible_device":3,"gpu_memory_utilization":0.20,"target_only":True}))
'''
        result = json.loads(
            checked(target, f"PROJECT={q(project)} {q(python)} -c {q(script)}")
        )
        print(json.dumps({"target_gate": gate, "patch": result}, indent=2))
    finally:
        target.close()


def python_header_probe_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        rows = {}
        for label, client in (("source", source), ("target", target)):
            home = checked(client, "printf '%s' \"$HOME\"")
            python = home.rstrip("/") + "/autodl-tmp/conda/envs/qwen-vllm/bin/python"
            code = r'''import json,pathlib,sys,sysconfig
paths=[]
for value in (sysconfig.get_path("include"),sysconfig.get_path("platinclude"),"/usr/include/python3.12","/usr/local/include/python3.12"):
 if value and value not in paths:paths.append(value)
print(json.dumps({"version":sys.version.split()[0],"soabi":sysconfig.get_config_var("SOABI"),"include_paths":[{"path":p.replace(str(pathlib.Path.home()),"[home]"),"python_h":(pathlib.Path(p)/"Python.h").is_file(),"pyconfig_h":(pathlib.Path(p)/"pyconfig.h").is_file()} for p in paths]}))
'''
            rows[label] = json.loads(checked(client, f"{q(python)} -c {q(code)}"))
        print(json.dumps({"source_gate": source_gate, "target_gate": target_gate, "python_headers": rows}, indent=2))
    finally:
        source.close()
        target.close()


def provision_target_python_headers_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        source_home = checked(source, "printf '%s' \"$HOME\"")
        target_home = checked(target, "printf '%s' \"$HOME\"")
        source_root = source_home.rstrip("/") + "/autodl-tmp"
        target_root = target_home.rstrip("/") + "/autodl-tmp"
        target_project = target_root + "/epidemic-openclaw"
        source_include = source_root + "/conda/envs/qwen-vllm/include"
        source_archive = source_root + "/epidemic-openclaw/storage/tmp/cpython312-headers.tar.gz"
        target_archive = target_root + "/.migration-staging/cpython312-headers.tar.gz"
        target_toolchain = target_root + "/toolchains/cpython-3.12.13"
        checked(
            source,
            f"test -f {q(source_include + '/python3.12/Python.h')} && "
            f"test -f {q(source_include + '/python3.12/pyconfig.h')} && "
            f"tar -C {q(source_include)} -czf {q(source_archive)} python3.12",
            timeout=300,
        )
        try:
            copied = stream_file(source, source_archive, target, target_archive, "transfer_cpython312_headers", 600)
            source_hash = sha256(source, source_archive)
            target_hash = sha256(target, target_archive)
            if source_hash != target_hash:
                raise MigrationError("PYTHON_HEADERS_HASH_MISMATCH")
            checked(
                target,
                f"mkdir -p {q(target_toolchain + '/include')} && "
                f"tar -xzf {q(target_archive)} -C {q(target_toolchain + '/include')} --no-same-owner && "
                f"test -f {q(target_toolchain + '/include/python3.12/Python.h')} && "
                f"test -f {q(target_toolchain + '/include/python3.12/pyconfig.h')}",
                timeout=300,
            )
            python = remote_python(target)
            code = r'''import json,os,pathlib
common=pathlib.Path(os.environ["PROJECT"])/"scripts/qwen_vllm/_common.sh"
include=os.environ["INCLUDE"]
line=f'export CPATH="{include}${{CPATH:+:$CPATH}}"'
text=common.read_text()
rows=[row for row in text.splitlines() if not row.startswith("export CPATH=")]
insert_at=next((i+1 for i,row in enumerate(rows) if row.startswith("export PATH=")),len(rows))
rows.insert(insert_at,line)
common.write_text("\n".join(rows)+"\n")
common.chmod(0o755)
print(json.dumps({"python_h":(pathlib.Path(include)/"Python.h").is_file(),"pyconfig_h":(pathlib.Path(include)/"pyconfig.h").is_file(),"cpath_process_scoped":True}))
'''
            result = json.loads(
                checked(
                    target,
                    f"PROJECT={q(target_project)} INCLUDE={q(target_toolchain + '/include/python3.12')} "
                    f"{q(python)} -c {q(code)}",
                )
            )
            result.update({"archive_bytes": copied, "sha256_match": True})
            print(json.dumps({"source_gate": source_gate, "target_gate": target_gate, "headers": result}, indent=2))
        finally:
            run(source, f"rm -f {q(source_archive)}", timeout=60)
            run(target, f"rm -f {q(target_archive)}", timeout=60)
    finally:
        source.close()
        target.close()


def diagnose_target_dashboard_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        files = ("scripts/dashboard/start_dashboard.sh", "scripts/dashboard/status_dashboard.sh", "scripts/dashboard/stop_dashboard.sh")
        scripts = {}
        for rel in files:
            rc, value, _error = run(target, f"sed -n '1,260p' {q(project + '/' + rel)}", timeout=60)
            scripts[rel] = [line.replace(home, "[target-home]") for line in value.splitlines()] if rc == 0 else ["missing"]
        rc, output, error = run(
            target,
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            + q(project + "/scripts/dashboard/start_dashboard.sh"),
            timeout=300,
        )
        python = remote_python(target)
        code = r'''import json,os,pathlib,re,socket
project=pathlib.Path(os.environ["PROJECT"]);home=os.environ["HOME_PATH"]
def clean(line):
 line=line.replace(home,"[target-home]")
 line=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",line)
 return line[-700:]
logs=[]
for p in sorted((project/"storage/logs").rglob("*"),key=lambda x:x.stat().st_mtime if x.is_file() else 0):
 if p.is_file() and "dashboard" in str(p).lower():
  try:lines=p.read_text(errors="replace").splitlines()[-80:]
  except Exception:continue
  logs.append({"file":str(p.relative_to(project)),"tail":[clean(x) for x in lines]})
def port_listening(port):
 s=socket.socket();s.settimeout(.3)
 try:return s.connect_ex(("127.0.0.1",port))==0
 finally:s.close()
print(json.dumps({"logs":logs[-5:],"port_8020":port_listening(8020)}))
'''
        details = json.loads(
            checked(target, f"PROJECT={q(project)} HOME_PATH={q(home)} {q(python)} -c {q(code)}", timeout=120)
        )
        safe = []
        for line in (output + "\n" + error).splitlines():
            line = line.replace(home, "[target-home]")
            line = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[redacted]", line)
            safe.append(line[-700:])
        print(json.dumps({"target_gate": gate, "scripts": scripts, "start_rc": rc, "start_output": safe[-80:], "details": details}, indent=2))
    finally:
        target.close()


def patch_target_service_users_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        python = remote_python(target)
        code = r'''import json,os,pathlib,re
scripts=pathlib.Path(os.environ["PROJECT"])/"scripts"
changed=[];removed_runuser=0;removed_chown=0;removed_install_owner=0
for path in scripts.rglob("*.sh"):
 text=path.read_text(errors="strict")
 rows=[];local_runuser=0;local_chown=0;local_install_owner=0
 for row in text.splitlines():
  updated,n=re.subn(r"\brunuser\s+-u\s+epidemic[a-z0-9_-]*\s+--\s+", "", row)
  local_runuser+=n
  updated,n=re.subn(r"\s+-o\s+epidemic[a-z0-9_-]*\s+-g\s+epidemic[a-z0-9_-]*", "", updated)
  local_install_owner+=n
  if re.match(r"^\s*chown\s+epidemic[a-z0-9_-]*:epidemic[a-z0-9_-]*\s+",updated):
   local_chown+=1;continue
  rows.append(updated)
 if local_runuser or local_chown or local_install_owner:
  path.write_text("\n".join(rows)+"\n");path.chmod(path.stat().st_mode|0o100)
  changed.append(str(path.relative_to(scripts)))
  removed_runuser+=local_runuser;removed_chown+=local_chown;removed_install_owner+=local_install_owner
remaining=[]
for path in scripts.rglob("*.sh"):
 for number,row in enumerate(path.read_text(errors="ignore").splitlines(),1):
  if re.search(r"\brunuser\s+-u\s+epidemic|^\s*chown\s+epidemic|\s+-o\s+epidemic",row):remaining.append(f"{path.relative_to(scripts)}:{number}")
print(json.dumps({"changed_files":changed,"removed_runuser":removed_runuser,"removed_chown":removed_chown,"removed_install_owner":removed_install_owner,"remaining_service_user_operations":remaining,"target_only":True}))
'''
        result = json.loads(checked(target, f"PROJECT={q(project)} {q(python)} -c {q(code)}"))
        print(json.dumps({"target_gate": gate, "patch": result}, indent=2))
    finally:
        target.close()


def diagnose_target_review_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
        files = ("scripts/source_review/_common.sh", "scripts/source_review/start_source_review.sh", "scripts/source_review/status_source_review.sh", "scripts/source_review/stop_source_review.sh")
        scripts = {}
        for rel in files:
            rc, value, _error = run(target, f"sed -n '1,300p' {q(project + '/' + rel)}", timeout=60)
            lines=[]
            for line in value.splitlines():
                line=line.replace(home,"[target-home]")
                line=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",line)
                lines.append(line)
            scripts[rel] = lines if rc == 0 else ["missing"]
        rc, output, error = run(
            target,
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            + q(project + "/scripts/source_review/start_source_review.sh"),
            timeout=300,
        )
        python = remote_python(target)
        code = r'''import json,os,pathlib,re,socket
project=pathlib.Path(os.environ["PROJECT"]);home=os.environ["HOME_PATH"]
def clean(line):
 line=line.replace(home,"[target-home]")
 line=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",line)
 return line[-700:]
logs=[]
for p in sorted((project/"storage/logs").rglob("*"),key=lambda x:x.stat().st_mtime if x.is_file() else 0):
 if p.is_file() and ("review" in str(p).lower() or "source-review" in str(p).lower()):
  try:lines=p.read_text(errors="replace").splitlines()[-100:]
  except Exception:continue
  logs.append({"file":str(p.relative_to(project)),"tail":[clean(x) for x in lines]})
def port_listening(port):
 s=socket.socket();s.settimeout(.3)
 try:return s.connect_ex(("127.0.0.1",port))==0
 finally:s.close()
print(json.dumps({"logs":logs[-5:],"port_8021":port_listening(8021)}))
'''
        details = json.loads(checked(target, f"PROJECT={q(project)} HOME_PATH={q(home)} {q(python)} -c {q(code)}", timeout=120))
        safe=[]
        for line in (output+"\n"+error).splitlines():
            line=line.replace(home,"[target-home]")
            line=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",line)
            safe.append(line[-700:])
        print(json.dumps({"target_gate":gate,"scripts":scripts,"start_rc":rc,"start_output":safe[-100:],"details":details},indent=2))
    finally:
        target.close()


def migration_acceptance_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        source_home = checked(source, "printf '%s' \"$HOME\"")
        target_home = checked(target, "printf '%s' \"$HOME\"")
        projects = {
            "source": source_home.rstrip("/") + "/autodl-tmp/epidemic-openclaw",
            "target": target_home.rstrip("/") + "/autodl-tmp/epidemic-openclaw",
        }
        roots = {
            "source": source_home.rstrip("/") + "/autodl-tmp",
            "target": target_home.rstrip("/") + "/autodl-tmp",
        }
        clients = {"source": source, "target": target}
        services = (
            ("postgres", "scripts/postgres/status.sh"),
            ("vllm", "scripts/qwen_vllm/status.sh"),
            ("query_api", "scripts/query_api/status_query_api.sh"),
            ("retrieval_worker", "scripts/retrieval_worker/status_retrieval_worker.sh"),
            ("openclaw", "scripts/openclaw/status_openclaw.sh"),
            ("dashboard", "scripts/dashboard/status_dashboard.sh"),
            ("discovery_worker", "scripts/source_discovery_worker/status_source_discovery_worker.sh"),
            ("promotion_worker", "scripts/source_promotion_worker/status_source_promotion_worker.sh"),
            ("review_console", "scripts/source_review/status_source_review.sh"),
            ("daily_update", "scripts/daily_update/status_daily_update.sh"),
        )

        def sql(label: str, statement: str) -> str:
            client = clients[label]
            project = projects[label]
            root = roots[label]
            psql = root + "/conda/envs/postgresql18/bin/psql"
            if label == "source":
                base = f"runuser -u epidemicdb -- {q(psql)} -h {q(project + '/storage/database/postgresql18/run')} -U epidemicdb -d epidemic_openclaw -Atc {q(statement)}"
                return checked(client, base, timeout=300)
            python = root + "/conda/envs/epidemic-core/bin/python"
            env_script = project + "/scripts/database/_env.sh"
            query_code = r'''import os,sys,sqlalchemy as sa
engine=sa.create_engine(os.environ["MIGRATION_DATABASE_URL"],pool_pre_ping=True)
with engine.connect() as connection:value=connection.execute(sa.text(sys.stdin.read())).scalar_one()
print(value)
'''
            command = f"bash -c 'source \"$1\"; exec \"$2\" -c \"$3\"' sh {q(env_script)} {q(python)} {q(query_code)}"
            rc, output, error = run(client, command, timeout=300, input_text=statement)
            if rc:
                raise MigrationError(f"TARGET_RESTRICTED_DATABASE_QUERY_FAILED:{rc}:{error[-200:]}")
            return output.strip()

        state = {}
        counts_sql = "SELECT json_build_object(" + ",".join(
            f"'{name}',(SELECT count(*) FROM epidemic.{name})"
            for name in (
                "data_sources", "query_requests", "retrieval_runs",
                "source_fetches", "raw_artifacts", "normalized_observations",
                "quality_assessments", "dataset_snapshots", "dataset_snapshot_observations",
            )
        ) + ")::text"
        snapshots_sql = "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"
        listener_code = r'''import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":");port=int(port_hex,16)
  if port in ports and parts[3]=="0A":rows.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))
'''
        process_proxy_code = r'''import json,os,pathlib
root=os.environ["PROJECT"].encode();names={b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY",b"GIT_CONFIG"};hits=[]
for path in pathlib.Path("/proc").iterdir():
 if not path.name.isdigit():continue
 try:cmd=(path/"cmdline").read_bytes();env=(path/"environ").read_bytes().split(b"\0")
 except (PermissionError,FileNotFoundError,ProcessLookupError):continue
 if root in cmd and any(row.partition(b"=")[0] in names for row in env):hits.append(path.name)
print(json.dumps({"project_processes_with_proxy":len(hits)}))
'''
        for label in ("source", "target"):
            client = clients[label]
            project = projects[label]
            root = roots[label]
            python = root + "/conda/envs/epidemic-core/bin/python"
            service_state = {
                name: run(client, q(project + "/" + rel), timeout=180)[0] == 0
                for name, rel in services
            }
            listeners = json.loads(checked(client, f"{q(python)} -c {q(listener_code)}"))
            counts = json.loads(sql(label, counts_sql))
            snapshots = json.loads(sql(label, snapshots_sql))
            head = checked(client, f"cd {q(project)} && git rev-parse HEAD")
            dirty = checked(client, f"cd {q(project)} && git status --porcelain=v1 -uall", timeout=120).splitlines()
            diff_ok = run(client, f"cd {q(project)} && git diff --check", timeout=120)[0] == 0
            shell_proxy = checked(client, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]] && echo absent || echo present'")
            process_proxy = json.loads(checked(client, f"PROJECT={q(project)} {q(python)} -c {q(process_proxy_code)}"))
            secret_pattern = 'REDACTED'
            tls_pattern = r"curl[[:space:]]+-k|wget[[:space:]].*--no-check-certificate|GIT_SSL_NO_VERIFY[=:][[:space:]]*true|pip[[:space:]].*--trusted-host"
            secret_count = int(checked(client, f"cd {q(project)} && (rg -Il --hidden -g '!.git/**' -g '!storage/**' -g '!.secrets/**' -g '!backups/**' -g '!reports/**' -e {q(secret_pattern)} . || true) | wc -l"))
            tls_count = int(checked(client, f"cd {q(project)} && (rg -Il --hidden -g '!.git/**' -g '!storage/**' -g '!.secrets/**' -g '!backups/**' -g '!reports/**' -e {q(tls_pattern)} . || true) | wc -l"))
            state[label] = {
                "services": service_state,
                "listeners": listeners,
                "migration": sql(label, "SELECT version_num FROM epidemic.alembic_version"),
                "counts": counts,
                "snapshots": snapshots,
                "git_head": head,
                "git_dirty_count": len(dirty),
                "git_diff_check": diff_ok,
                "shell_proxy": shell_proxy,
                **process_proxy,
                "secret_scan_hits": secret_count,
                "insecure_tls_hits": tls_count,
            }

        target_python = roots["target"] + "/conda/envs/epidemic-core/bin/python"
        http_code = r'''import json,time,urllib.request
def get(url):
 try:
  with urllib.request.urlopen(url,timeout=20) as response:return response.status
 except Exception:return 0
def chat(prompt):
 body=json.dumps({"model":"qwen3-8b-awq-local","messages":[{"role":"user","content":prompt}],"temperature":0,"max_tokens":24}).encode()
 request=urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",data=body,headers={"Content-Type":"application/json"})
 started=time.monotonic()
 with urllib.request.urlopen(request,timeout=600) as response:value=json.load(response)
 content=value["choices"][0]["message"].get("content") or ""
 return {"nonempty":bool(content.strip()),"characters":len(content),"latency_seconds":round(time.monotonic()-started,3)}
print(json.dumps({"vllm_health":get("http://127.0.0.1:8000/health"),"dashboard_health":get("http://127.0.0.1:8020/healthz"),"review_health":get("http://127.0.0.1:8021/healthz"),"english":chat("Reply only: OK"),"chinese":chat("只回复：正常")}))
'''
        target_http = json.loads(checked(target, f"{q(target_python)} -c {q(http_code)}", timeout=1500))
        pg_smoke = run(target, q(projects["target"] + "/scripts/postgres/smoke_test.sh"), timeout=300)[0] == 0
        cli = projects["target"] + "/scripts/openclaw/openclaw_cli.sh"
        openclaw_version = checked(target, f"{q(cli)} --version", timeout=120)
        openclaw_probe_rc, _out, _err = run(
            target,
            f"{q(cli)} agent --session-key agent:main:a100-migration-smoke --message {q('Reply only OK.')} --thinking off --json",
            timeout=900,
        )
        security = {}
        for name, suffix in (("standard", ""), ("deep", " --deep")):
            rc, output, _error = run(target, f"{q(cli)} security audit{suffix} --json", timeout=300)
            summary = None
            if rc == 0:
                try:summary = json.loads(output).get("summary")
                except json.JSONDecodeError:summary = {"parseable": False}
            security[name] = {"rc": rc, "summary": summary}
        qwen_python = roots["target"] + "/conda/envs/qwen-vllm/bin/python"
        version_code = r'''import importlib.metadata,json,torch
print(json.dumps({"vllm":importlib.metadata.version("vllm"),"torch":torch.__version__,"cuda":torch.version.cuda,"cuda_available":torch.cuda.is_available(),"gpu":torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"capability":torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None}))
'''
        runtime = {
            "core_python": checked(target, f"{q(target_python)} --version"),
            "postgres": checked(target, f"{q(roots['target'] + '/conda/envs/postgresql18/bin/postgres')} --version"),
            "node": checked(
                target,
                f"bash -c 'source \"$1\"; node --version' sh {q(projects['target'] + '/scripts/openclaw/openclaw_env.sh')}",
            ),
            "openclaw": openclaw_version,
            "qwen": json.loads(checked(target, f"CUDA_VISIBLE_DEVICES=3 {q(qwen_python)} -c {q(version_code)}", timeout=120)),
        }
        model_code = r'''import hashlib,json,os,pathlib
project=pathlib.Path(os.environ["PROJECT"]);model=project/"models/llm/Qwen3-8B-AWQ";manifest=json.loads((project/"config/qwen3-8b-awq-model-manifest.json").read_text());bad=[]
for row in manifest["files"]:
 p=model/row["path"]
 if not p.is_file() or p.stat().st_size!=row["size"]:bad.append(row["path"]+":size");continue
 h=hashlib.sha256()
 with p.open("rb") as handle:
  for chunk in iter(lambda:handle.read(8*1024*1024),b""):h.update(chunk)
 if h.hexdigest()!=row["sha256"]:bad.append(row["path"]+":sha256")
print(json.dumps({"model_id":manifest["model_id"],"revision":manifest["revision"],"file_count":len(manifest["files"]),"total_bytes":sum(row["size"] for row in manifest["files"]),"mismatches":bad}))
'''
        model = json.loads(checked(target, f"PROJECT={q(projects['target'])} {q(target_python)} -c {q(model_code)}", timeout=1800))
        comparisons = {
            "git_head_equal": state["source"]["git_head"] == state["target"]["git_head"],
            "migration_equal": state["source"]["migration"] == state["target"]["migration"],
            "counts_equal": state["source"]["counts"] == state["target"]["counts"],
            "snapshots_equal": state["source"]["snapshots"] == state["target"]["snapshots"],
        }
        result = {
            "source_gate": source_gate,
            "target_gate": target_gate,
            "state": state,
            "comparisons": comparisons,
            "target_functional": {"http": target_http, "postgres_smoke": pg_smoke, "openclaw_agent": openclaw_probe_rc == 0, "openclaw_security": security},
            "target_runtime": runtime,
            "target_model": model,
        }
        print(json.dumps(result, indent=2))
        expected_ports = {5432, 8000, 8010, 18789, 8020, 8021}
        for label in ("source", "target"):
            assert all(state[label]["services"].values())
            assert {row["port"] for row in state[label]["listeners"]} == expected_ports
            assert all(row["loopback"] for row in state[label]["listeners"])
            assert state[label]["migration"] == "0010_p1_8_daily_updates"
            assert state[label]["git_diff_check"]
            assert state[label]["shell_proxy"] == "absent"
            assert state[label]["project_processes_with_proxy"] == 0
            assert state[label]["secret_scan_hits"] == 0
            assert state[label]["insecure_tls_hits"] == 0
        assert all(comparisons.values())
        assert pg_smoke and openclaw_probe_rc == 0
        assert target_http["vllm_health"] == target_http["dashboard_health"] == target_http["review_health"] == 200
        assert target_http["english"]["nonempty"] and target_http["chinese"]["nonempty"]
        assert runtime["qwen"]["cuda_available"] and not model["mismatches"]
    finally:
        source.close()
        target.close()


def openclaw_security_findings_action() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        result = {}
        for label, client in (("source", source), ("target", target)):
            home = checked(client, "printf '%s' \"$HOME\"")
            project = home.rstrip("/") + "/autodl-tmp/epidemic-openclaw"
            cli = project + "/scripts/openclaw/openclaw_cli.sh"
            rows = {}
            for name, suffix in (("standard", ""), ("deep", " --deep")):
                rc, output, _error = run(client, f"{q(cli)} security audit{suffix} --json", timeout=300)
                value = json.loads(output) if output else {}
                findings = []
                for row in value.get("findings", []):
                    if not isinstance(row, dict):continue
                    findings.append({key: str(row.get(key)).replace(home,"[home]") for key in ("id","code","checkId","severity","title") if row.get(key) is not None})
                rows[name] = {"rc": rc, "summary": value.get("summary"), "findings": findings}
            result[label] = rows
        print(json.dumps({"source_gate":source_gate,"target_gate":target_gate,"security":result},indent=2))
    finally:
        source.close()
        target.close()


def target_regression_action() -> None:
    creds = credentials(TARGET_INFO)
    target, gate = connect_with_gate("target", creds)
    target.get_transport().set_keepalive(30)  # type: ignore[union-attr]
    try:
        home = checked(target, "printf '%s' \"$HOME\"")
        root = home.rstrip("/") + "/autodl-tmp"
        project = root + "/epidemic-openclaw"
        python = root + "/conda/envs/epidemic-core/bin/python"
        integration = project + "/integrations/openclaw/epidemic-query-tools"
        openclaw_env = project + "/scripts/openclaw/openclaw_env.sh"
        commands = (
            ("pytest_unit", f"cd {q(project)} && PYTHONPATH={q(project + '/src')} {q(python)} -m pytest -q tests/unit", 3600),
            ("ruff", f"cd {q(project)} && {q(python)} -m ruff check src tests migrations", 900),
            ("format", f"cd {q(project)} && {q(python)} -m ruff format --check src tests migrations", 900),
            ("mypy", f"cd {q(project)} && PYTHONPATH={q(project + '/src')} {q(python)} -m mypy --strict src", 1800),
            ("pip_check", f"{q(python)} -m pip check", 600),
            ("npm_build_test", f"bash -c 'source \"$1\"; cd \"$2\"; npm run build && npm test' sh {q(openclaw_env)} {q(integration)}", 1800),
            ("bash_syntax", f"cd {q(project)} && find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n", 900),
            ("git_diff_check", f"cd {q(project)} && git diff --check", 300),
        )
        results = {}
        for name, command, timeout in commands:
            rc, output, error = run(target, command, timeout=timeout)
            lines=[]
            for line in (output+"\n"+error).splitlines()[-40:]:
                line=line.replace(home,"[target-home]")
                line=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",line)
                lines.append(line[-700:])
            results[name]={"rc":rc,"tail":lines}
        status_rc = run(target, q(project + "/scripts/services/status_all.sh"), timeout=300)[0]
        print(json.dumps({"target_gate":gate,"checks":results,"services_after":status_rc==0},indent=2))
        assert all(row["rc"]==0 for row in results.values()) and status_rc==0
    finally:
        target.close()


def gate_and_audit() -> None:
    source_creds = credentials(SOURCE_INFO)
    target_creds = credentials(TARGET_INFO)
    source, source_gate = connect_with_gate("source", source_creds)
    target, target_gate = connect_with_gate("target", target_creds)
    try:
        target_sudo = target_gate["identity"] == "root" or sudo_available(target, target_creds)
        result: dict[str, object] = {
            "source_gate": source_gate,
            "target_gate": target_gate,
            "target_root_access": target_sudo,
            "source": audit(source, "source"),
        }
        if target_sudo:
            result["target"] = audit(
                target,
                "target",
                None if target_gate["identity"] == "root" else target_creds.password,
            )
        else:
            result["target"] = audit_user_target(target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        source.close()
        target.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "audit",
            "diagnose-target-info",
            "layout",
            "runtime",
            "migrate",
            "pack-diagnose",
            "cleanup-direct",
            "rebuild-manifest",
            "source-provenance",
            "rebuild-probe",
            "migrate-state",
            "resume-env-relocation",
            "restore-source-services",
            "resume-state-transfer",
            "rebuild-target",
            "target-pip-diagnose",
            "finish-qwen-runtime",
            "target-qwen-versions",
            "target-install-progress",
            "start-target-services",
            "reconcile-target-daily-startup",
            "diagnose-target-postgres",
            "attempt-target-postgres",
            "target-postgres-scripts",
            "patch-target-postgres-user-mode",
            "diagnose-target-vllm",
            "patch-target-legacy-python",
            "target-vllm-root-cause",
            "target-gpu-status",
            "patch-target-vllm-capacity",
            "python-header-probe",
            "provision-target-python-headers",
            "diagnose-target-dashboard",
            "patch-target-service-users",
            "diagnose-target-review",
            "migration-acceptance",
            "openclaw-security-findings",
            "target-regression",
        ),
    )
    args = parser.parse_args()
    if args.action == "audit":
        gate_and_audit()
    elif args.action == "diagnose-target-info":
        print(json.dumps(diagnose_info(TARGET_INFO), ensure_ascii=False, indent=2))
    elif args.action == "layout":
        layout_action()
    elif args.action == "runtime":
        runtime_action()
    elif args.action == "migrate":
        migrate_action()
    elif args.action == "pack-diagnose":
        pack_diagnose_action()
    elif args.action == "cleanup-direct":
        cleanup_stale_direct_action()
    elif args.action == "rebuild-manifest":
        rebuild_manifest_action()
    elif args.action == "source-provenance":
        source_provenance_action()
    elif args.action == "rebuild-probe":
        rebuild_probe_action()
    elif args.action == "migrate-state":
        migrate_state_action()
    elif args.action == "resume-env-relocation":
        resume_environment_relocation_action()
    elif args.action == "restore-source-services":
        restore_source_services_action()
    elif args.action == "resume-state-transfer":
        resume_state_transfer_action()
    elif args.action == "rebuild-target":
        rebuild_target_action()
    elif args.action == "target-pip-diagnose":
        target_pip_diagnose_action()
    elif args.action == "finish-qwen-runtime":
        finish_qwen_runtime_action()
    elif args.action == "target-qwen-versions":
        target_qwen_versions_action()
    elif args.action == "target-install-progress":
        target_install_progress_action()
    elif args.action == "start-target-services":
        start_target_services_action()
    elif args.action == "reconcile-target-daily-startup":
        reconcile_target_daily_startup_action()
    elif args.action == "diagnose-target-postgres":
        diagnose_target_postgres_action()
    elif args.action == "attempt-target-postgres":
        attempt_target_postgres_action()
    elif args.action == "target-postgres-scripts":
        target_postgres_scripts_action()
    elif args.action == "patch-target-postgres-user-mode":
        patch_target_postgres_user_mode_action()
    elif args.action == "diagnose-target-vllm":
        diagnose_target_vllm_action()
    elif args.action == "patch-target-legacy-python":
        patch_target_legacy_python_action()
    elif args.action == "target-vllm-root-cause":
        target_vllm_root_cause_action()
    elif args.action == "target-gpu-status":
        target_gpu_status_action()
    elif args.action == "patch-target-vllm-capacity":
        patch_target_vllm_capacity_action()
    elif args.action == "python-header-probe":
        python_header_probe_action()
    elif args.action == "provision-target-python-headers":
        provision_target_python_headers_action()
    elif args.action == "diagnose-target-dashboard":
        diagnose_target_dashboard_action()
    elif args.action == "patch-target-service-users":
        patch_target_service_users_action()
    elif args.action == "diagnose-target-review":
        diagnose_target_review_action()
    elif args.action == "migration-acceptance":
        migration_acceptance_action()
    elif args.action == "openclaw-security-findings":
        openclaw_security_findings_action()
    elif args.action == "target-regression":
        target_regression_action()


if __name__ == "__main__":
    main()
