"""Export a local project snapshot without copying credentials or opaque backups."""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import zipfile
import faulthandler

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "deliveries" / "DiseaseX_Nexus_local_20261019"
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".agents",
    ".codex", ".codex_tmp", ".codex_doc_review", ".runtime", "secrets",
    "private", "logs", "runtime_logs", ".streamlit_home", ".streamlit-demo",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "deliveries",
}
OPAQUE = {
    ".zip", ".7z", ".rar", ".tar", ".tgz", ".gz", ".bz2", ".xz",
    ".db", ".sqlite", ".sqlite3", ".dump", ".pgcustom", ".sql",
    ".docx", ".doc", ".pptx", ".ppt", ".exe", ".dll", ".pyd",
    ".pyc", ".pem", ".key", ".pfx", ".p12", ".pid", ".log",
    ".ai", ".emf", ".wmf",
}
BINARY = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ico",
    ".ttf", ".otf", ".woff", ".woff2", ".npy", ".npz", ".parquet",
    ".feather", ".pt", ".pth", ".onnx", ".safetensors", ".pkl", ".joblib",
    ".xlsx", ".xls", ".rds", ".rdata",
}
IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
KEY_BLOCK = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----", re.S)
# Redact literal credentials, preserving variable names and expression structure.
ASSIGN = re.compile(
    r'''(?ix)(?P<prefix>["']?\b(?:[\w-]{0,80}(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|private[_-]?key|ssh[_-]?user|username|user|token)[\w-]{0,80})["']?\s{0,20}[:=]\s{0,20})(?P<quote>["'])(?P<value>[^\r\n]{0,4096}?)(?P=quote)'''
)
ENV_ASSIGN = re.compile(r"(?im)^(\s*(?:export\s+)?[A-Z_]*(?:PASSWORD|PASSWD|SECRET|API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SSH_USER|USERNAME)[A-Z_]*\s*=\s*)([^\r\n]+)$")
AUTH = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_+/=.-]{12,}")
TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")
USERHOST = re.compile(r"(?i)(?<![\w.])(?:[a-z_][\w.-]*)@(?=(?:\d{1,3}\.){3}\d{1,3}|SERVER_HOST|\[SERVER_HOST\])")
HOME_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[^\\/\s\"'<>]+|USER_HOME/\s\"'<>]+|/root)(?=[\\/]|\b)")
KEY_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]|/)[^\r\n\"'<>]{0,300}?(?:\.ssh[\\/][^\s\"'<>]{1,150}|id_(?:rsa|ed25519|ecdsa)(?:\.pub)?|[^\\/\s\"'<>]{1,150}\.(?:pem|pfx|p12))")


def valid_ip(match: re.Match[str]) -> str:
    try:
        value = ipaddress.ip_address(match.group())
        return match.group() if value.is_loopback or value.is_unspecified else "[SERVER_HOST]"
    except ValueError:
        return match.group()


def sanitize(text: str) -> tuple[str, bool]:
    original = text
    text = KEY_BLOCK.sub("[PRIVATE_KEY_REMOVED]", text)
    text = USERHOST.sub("[SERVER_USER]@", text)
    text = IP.sub(valid_ip, text)
    text = KEY_PATH.sub("[PRIVATE_KEY_PATH]", text)
    text = HOME_PATH.sub("[USER_HOME]", text)
    text = ASSIGN.sub(lambda m: m["prefix"] + m["quote"] + "REDACTED" + m["quote"], text)
    text = ENV_ASSIGN.sub(r"\1REDACTED", text)
    text = AUTH.sub(r"\1 REDACTED", text)
    text = TOKEN.sub("REDACTED_TOKEN", text)
    # URL userinfo can hold database passwords as well as HTTP credentials.
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/\"'<>@]+@", r"\1REDACTED@", text)
    # Command-line credential arguments (quoted or a single shell token).
    text = re.sub(r'''(?ix)((?:--(?:password|passwd|token|api-key|username)|-(?:Credential|UserName|Password))\s+)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s;]+)''', r"\1REDACTED", text)
    return text, text != original


def exclusion(path: Path, rel: str) -> str | None:
    name = path.name.lower()
    if path.is_symlink():
        return "symlink"
    if "服务器信息" in name or "密码" in name or "用户名" in name:
        return "credential_file"
    if any(x in name for x in ("credential", "known_hosts", "id_rsa", "id_ed25519")):
        return "credential_file"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "credential_file"
    if path.suffix.lower() in OPAQUE or re.search(r"\.part\d+$", name):
        return "opaque_backup_database_or_nonproject_document"
    if path.suffix.lower() in {".sha256", ".sha", ".md5"}:
        return "original_checksum_not_applicable_to_export"
    if "screenshot" in rel.lower() and any(x in rel.lower() for x in ("auth", "login", "server", "credential")):
        return "potential_sensitive_screenshot"
    if sanitize(rel)[1]:
        return "sensitive_filename"
    return None


def main() -> None:
    faulthandler.dump_traceback_later(30, repeat=True)
    DEST.mkdir(parents=True, exist_ok=True)
    archive = DEST / "DiseaseX_Nexus_Code_Data_20261019.zip"
    manifest = []
    omitted = collections.Counter()
    changed = 0
    total_bytes = 0
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True) as package:
        for base, dirs, files in os.walk(ROOT, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info") and not Path(base, d).is_symlink())
            for filename in sorted(files):
                path = Path(base, filename)
                rel = path.relative_to(ROOT).as_posix()
                reason = exclusion(path, rel)
                if reason:
                    omitted[reason] += 1
                    continue
                try:
                    before = path.stat()
                    payload = path.read_bytes()
                    redacted = False
                    if path.suffix.lower() not in BINARY:
                        if b"\x00" in payload[:8192]:
                            # UTF-16 text is common in PowerShell exports.
                            if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
                                content, redacted = sanitize(payload.decode("utf-16"))
                                payload = content.encode("utf-8")
                            else:
                                omitted["unrecognized_binary"] += 1
                                continue
                        else:
                            try:
                                content = payload.decode("utf-8-sig")
                            except UnicodeDecodeError:
                                try:
                                    content = payload.decode("gb18030")
                                except UnicodeDecodeError:
                                    omitted["unrecognized_encoding"] += 1
                                    continue
                            content, redacted = sanitize(content)
                            if redacted:
                                payload = content.encode("utf-8")
                    after = path.stat()
                    if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
                        omitted["changed_during_export"] += 1
                        continue
                    package.writestr("project/" + rel, payload)
                    digest = hashlib.sha256(payload).hexdigest()
                    manifest.append({"path": rel, "bytes": len(payload), "sha256": digest, "redacted": redacted})
                    total_bytes += len(payload)
                    changed += int(redacted)
                    if len(manifest) % 2000 == 0:
                        print(json.dumps({"packaged_files": len(manifest), "source_mb": round(total_bytes / 1024**2)}, ensure_ascii=True), flush=True)
                except (OSError, ValueError) as exc:
                    omitted["unreadable_" + type(exc).__name__] += 1
        readme = f"""DiseaseX Nexus 本地代码与数据交付包

导出时间：{dt.datetime.now(dt.timezone.utc).isoformat()}
范围：当前本地工作目录中的可导出代码、数据、实验结果、图谱、配置模板和文档。
本包未连接服务器，不代表远端代码或数据库的最新状态。

出于项目 AGENTS.md 的凭据保护要求，未打包凭据目录、实际环境变量、密钥、连接凭据、运行日志、虚拟环境、缓存和版本控制目录。
历史压缩包/压缩分卷、数据库文件及转储、Office论文/幻灯片原件亦未收入；这些容器可能混有认证信息且包含重复备份。其在普通文件中已有的代码、CSV/JSON数据及实验输出仍按规则收录。
未识别的二进制格式、读取失败或打包期间变化的文件会跳过。
文本中的检测到的服务地址、连接用户名、用户目录、私钥路径及认证字面量已替换为占位符；原始工作目录未修改。脱敏可能影响部署脚本，使用时请在本机重新配置。
疾病观测中的日期、病例计数、指标及公共来源URL按原文件保留（命中敏感信息规则的内容除外）。

收入文件：{len(manifest)}
收入文件未压缩字节：{total_bytes}
含脱敏修改文件：{changed}
排除文件分类计数：{json.dumps(dict(omitted), ensure_ascii=False)}

project/README.md 和 project/pyproject.toml / requirements.txt 提供运行及依赖说明。
MANIFEST.json 记录每个收入文件的 SHA256 和脱敏状态，可用于核对文件完整性。
本包为本地文件快照，未执行启动或模型训练验证。
"""
        package.writestr("交付说明.txt", readme.encode("utf-8"))
        package.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps({"phase": "archive_written", "files": len(manifest), "bytes": total_bytes, "redacted_files": changed, "excluded": dict(omitted)}, ensure_ascii=True), flush=True)
    with zipfile.ZipFile(archive) as package:
        bad = package.testzip()
        if bad is not None:
            raise RuntimeError("Archive CRC validation failed")
        for item in manifest:
            if hashlib.sha256(package.read("project/" + item["path"])).hexdigest() != item["sha256"]:
                raise RuntimeError("Manifest hash validation failed")
    digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    (DEST / "SHA256.txt").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    (DEST / "交付说明.txt").write_text(readme, encoding="utf-8")
    print(json.dumps({"status": "verified", "archive": str(archive), "zip_bytes": archive.stat().st_size, "files": len(manifest), "sha256": digest}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
