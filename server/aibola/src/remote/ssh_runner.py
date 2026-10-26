from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.remote.sanitizer import sanitize_text
from src.remote.server_config import ServerConfig
from src.utils.io import ensure_parent


@dataclass
class RemoteResult:
    returncode: int
    stdout: str
    stderr: str
    command: str
    dry_run: bool = False


class SSHRunner:
    def __init__(self, config: ServerConfig, timeout: int = 60, dry_run: bool = False):
        self.config = config
        self.timeout = timeout
        self.dry_run = dry_run

    def _sanitize(self, text: str) -> str:
        return sanitize_text(text, self.config.secrets)

    def run(self, command: str, log_path: str | Path | None = None) -> RemoteResult:
        if self.dry_run:
            result = RemoteResult(0, self._sanitize(f"DRY RUN remote command: {command}\n"), "", command, True)
            self._write_log(log_path, result)
            return result
        if self.config.password:
            return self._run_paramiko(command, log_path)
        return self._run_system_ssh(command, log_path)

    def _run_system_ssh(self, command: str, log_path: str | Path | None = None) -> RemoteResult:
        target = f"{self.config.user}@{self.config.host}"
        args = ["ssh", "-p", str(self.config.port)]
        if self.config.key_file:
            args.extend(["-i", self.config.key_file])
        args.extend([target, command])
        completed = subprocess.run(args, capture_output=True, text=True, timeout=self.timeout, check=False)
        result = RemoteResult(
            completed.returncode,
            self._sanitize(completed.stdout),
            self._sanitize(completed.stderr),
            "ssh <remote-server> <command>",
        )
        self._write_log(log_path, result)
        return result

    def _run_paramiko(self, command: str, log_path: str | Path | None = None) -> RemoteResult:
        try:
            import paramiko  # type: ignore
        except Exception:
            result = RemoteResult(
                127,
                "",
                "Paramiko is required for password-based SSH on this local environment.",
                "paramiko <remote-server> <command>",
            )
            self._write_log(log_path, result)
            return result
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                password=self.config.password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=False,
            )
            _, stdout, stderr = client.exec_command(command, timeout=self.timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            status = stdout.channel.recv_exit_status()
            result = RemoteResult(status, self._sanitize(out), self._sanitize(err), "paramiko <remote-server> <command>")
            self._write_log(log_path, result)
            return result
        except Exception as exc:
            result = RemoteResult(1, "", self._sanitize(f"{type(exc).__name__}: remote command failed"), "paramiko <remote-server> <command>")
            self._write_log(log_path, result)
            return result
        finally:
            client.close()

    def sftp_put_dir(self, local_dir: str | Path, remote_dir: str | None = None, include_data: bool = False) -> RemoteResult:
        if self.dry_run:
            return RemoteResult(0, "DRY RUN remote sync\n", "", "sftp <local> <remote>", True)
        try:
            import paramiko  # type: ignore
        except Exception:
            return RemoteResult(127, "", "Paramiko is required for password-based SFTP sync.", "sftp <local> <remote>")
        local_root = Path(local_dir).resolve()
        remote_root = remote_dir or self.config.remote_dir
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        transferred = 0
        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                password=self.config.password,
                timeout=self.timeout,
                look_for_keys=False,
            )
            sftp = client.open_sftp()
            remote_root = self._resolve_sftp_remote_root(sftp, remote_root)
            self.run(f"mkdir -p {remote_root}")
            for path in local_root.rglob("*"):
                if should_exclude(path, local_root, include_data=include_data):
                    continue
                rel = path.relative_to(local_root).as_posix()
                target = f"{remote_root.rstrip('/')}/{rel}"
                if path.is_dir():
                    self._sftp_mkdirs(sftp, target)
                else:
                    self._sftp_mkdirs(sftp, target.rsplit("/", 1)[0])
                    sftp.put(str(path), target)
                    transferred += 1
            sftp.close()
            return RemoteResult(0, f"Transferred {transferred} files\n", "", "sftp sync <project> <remote>")
        except Exception as exc:
            return RemoteResult(1, "", self._sanitize(f"{type(exc).__name__}: remote sync failed"), "sftp sync <project> <remote>")
        finally:
            client.close()

    def sftp_put_selected(self, local_root: str | Path, remote_dir: str, relative_paths: Iterable[str]) -> RemoteResult:
        if self.dry_run:
            return RemoteResult(0, "DRY RUN selected remote sync\n", "", "sftp selected <local> <remote>", True)
        try:
            import paramiko  # type: ignore
        except Exception:
            return RemoteResult(127, "", "Paramiko is required for selected SFTP sync.", "sftp selected <local> <remote>")
        local_base = Path(local_root).resolve()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        transferred = 0
        missing: list[str] = []
        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                password=self.config.password,
                timeout=self.timeout,
                look_for_keys=False,
            )
            sftp = client.open_sftp()
            remote_root = self._resolve_sftp_remote_root(sftp, remote_dir)
            self._sftp_mkdirs(sftp, remote_root)
            for rel in relative_paths:
                src = local_base / rel
                if not src.exists():
                    missing.append(rel)
                    continue
                paths = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()]
                for path in paths:
                    if should_exclude(path, local_base, include_data=True):
                        continue
                    rel_path = path.relative_to(local_base).as_posix()
                    target = f"{remote_root.rstrip('/')}/{rel_path}"
                    self._sftp_mkdirs(sftp, target.rsplit("/", 1)[0])
                    sftp.put(str(path), target)
                    transferred += 1
            sftp.close()
            warning = f" Missing: {', '.join(missing)}\n" if missing else ""
            return RemoteResult(0, f"Transferred {transferred} files\n{warning}", "", "sftp selected sync <project> <remote>")
        except Exception as exc:
            return RemoteResult(1, "", self._sanitize(f"{type(exc).__name__}: remote selected sync failed"), "sftp selected sync <project> <remote>")
        finally:
            client.close()

    def sftp_put_file(self, local_file: str | Path, remote_file: str) -> RemoteResult:
        if self.dry_run:
            return RemoteResult(0, "DRY RUN put remote file\n", "", "sftp put <local-file> <remote-file>", True)
        try:
            import paramiko  # type: ignore
        except Exception:
            return RemoteResult(127, "", "Paramiko is required for SFTP put.", "sftp put <local-file> <remote-file>")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                password=self.config.password,
                timeout=self.timeout,
                look_for_keys=False,
            )
            sftp = client.open_sftp()
            target = self._resolve_sftp_remote_root(sftp, remote_file)
            self._sftp_mkdirs(sftp, target.rsplit("/", 1)[0])
            sftp.put(str(local_file), target)
            sftp.close()
            return RemoteResult(0, "Transferred 1 file\n", "", "sftp put <local-file> <remote-file>")
        except Exception as exc:
            return RemoteResult(1, "", self._sanitize(f"{type(exc).__name__}: remote file put failed"), "sftp put <local-file> <remote-file>")
        finally:
            client.close()

    def sftp_get_file(self, remote_file: str, local_file: str | Path) -> RemoteResult:
        if self.dry_run:
            return RemoteResult(0, "DRY RUN get remote file\n", "", "sftp get <remote-file> <local-file>", True)
        try:
            import paramiko  # type: ignore
        except Exception:
            return RemoteResult(127, "", "Paramiko is required for SFTP get.", "sftp get <remote-file> <local-file>")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        tmp_path: Path | None = None
        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                password=self.config.password,
                timeout=self.timeout,
                look_for_keys=False,
            )
            sftp = client.open_sftp()
            source = self._resolve_sftp_remote_root(sftp, remote_file)
            local_path = ensure_parent(local_file)
            tmp_path = local_path.with_name(local_path.name + ".tmp")
            if tmp_path.exists():
                tmp_path.unlink()
            sftp.get(source, str(tmp_path))
            tmp_path.replace(local_path)
            sftp.close()
            return RemoteResult(0, "Pulled 1 file\n", "", "sftp get <remote-file> <local-file>")
        except Exception as exc:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return RemoteResult(1, "", self._sanitize(f"{type(exc).__name__}: remote file get failed"), "sftp get <remote-file> <local-file>")
        finally:
            client.close()

    @staticmethod
    def _write_log(log_path: str | Path | None, result: RemoteResult) -> None:
        if not log_path:
            return
        p = ensure_parent(log_path)
        p.write_text(
            f"command={result.command}\nreturncode={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _sftp_mkdirs(sftp, remote_path: str) -> None:
        parts = [part for part in remote_path.replace("\\", "/").split("/") if part and part != "~"]
        if remote_path.startswith("~/"):
            current = "~"
        elif remote_path.startswith("/"):
            current = "/"
        else:
            current = ""
        for part in parts:
            if current == "/":
                current = f"/{part}"
            else:
                current = f"{current}/{part}" if current else part
            try:
                sftp.mkdir(current)
            except OSError:
                pass

    @staticmethod
    def _resolve_sftp_remote_root(sftp, remote_root: str) -> str:
        if remote_root == "~":
            return sftp.normalize(".")
        if remote_root.startswith("~/"):
            return sftp.normalize(".").rstrip("/") + "/" + remote_root[2:]
        return remote_root


def should_exclude(path: Path, root: Path, include_data: bool = False) -> bool:
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    excluded_top_dirs = {
        ".agents",
        ".codex",
        ".git",
        ".pytest_cache",
        "lora_weights",
        "logs",
        "mlruns",
        "models",
        "wandb",
        "__pycache__",
    }
    if parts and parts[0] in excluded_top_dirs:
        return True
    if "__pycache__" in parts:
        return True
    if not include_data and (rel.startswith("data/raw/") or rel.startswith("data/interim/") or rel.startswith("data/processed/")):
        return True
    name = path.name
    if len(parts) == 1:
        return (
            name.startswith(".env")
            or name.startswith("server")
            or name.startswith("remote")
            or name.startswith("id_rsa")
            or "服务器信息" in name
            or name.endswith((".pem", ".key"))
        )
    return name.endswith((".pem", ".key")) or name.startswith("id_rsa")
