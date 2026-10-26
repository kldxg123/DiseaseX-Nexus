from __future__ import annotations

import argparse
import shlex

from src.remote.p0b_utils import LOCAL_REPORTS, remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


DEFAULT_SOURCE = "~/.cache/huggingface/hub/models--Qwen--Qwen2.5-7B-Instruct/snapshots/a09a35458c702b33eeacc393d103063234e8bc28"


REMOTE_STAGE = r"""
python3 - <<'PY'
import hashlib
import json
import os
import shutil
from pathlib import Path

from src.models.qwen25_local_model_finder import validate_candidate

REPORT_DIR = Path("data/outputs/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DEST = Path("models/Qwen2.5-7B-Instruct")
SMALL_HASH_LIMIT = 128 * 1024 * 1024
SAFETENSORS_HASH_LIMIT = 512 * 1024 * 1024
SECRET_NAME_MARKERS = (".env", "hf_token", "id_rsa", ".pem", ".key", "passwd", "password")


def sanitize(path):
    home = Path.home().resolve().as_posix()
    try:
        text = Path(path).resolve().as_posix()
    except Exception:
        text = str(path)
    if text == home:
        return "$HOME"
    if text.startswith(home + "/"):
        return "$HOME/" + text[len(home) + 1 :]
    return "<outside-home>/" + Path(text).name


def sha256(path, max_bytes=None):
    if max_bytes is not None and path.stat().st_size > max_bytes:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def is_secret_like(path):
    lowered = path.name.lower()
    return any(marker in lowered for marker in SECRET_NAME_MARKERS)


def build_source_manifest(root):
    files = []
    skipped = []
    broken = []
    symlink_count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if is_secret_like(path):
            skipped.append({"relative_path": rel, "reason": "secret_like_filename"})
            continue
        if path.is_symlink():
            symlink_count += 1
        try:
            source = path.resolve(strict=True)
        except OSError:
            broken.append({"relative_path": rel, "reason": "broken_symlink"})
            continue
        if not source.is_file():
            skipped.append({"relative_path": rel, "reason": "not_regular_file"})
            continue
        suffix = source.suffix.lower()
        limit = SAFETENSORS_HASH_LIMIT if suffix == ".safetensors" else SMALL_HASH_LIMIT
        digest = sha256(source, max_bytes=limit)
        files.append(
            {
                "relative_path": rel,
                "source_path_internal": source.as_posix(),
                "size_bytes": source.stat().st_size,
                "sha256": digest,
                "hash_status": "sha256" if digest else "size_only_for_large_file",
                "resolved_from_symlink": path.is_symlink(),
            }
        )
    status = "ok"
    blocked_reason = "none"
    if broken:
        status = "blocked"
        blocked_reason = "broken_symlink_in_source_snapshot"
    if not any(item["relative_path"].endswith(".safetensors") for item in files):
        status = "blocked"
        blocked_reason = "missing_safetensors_in_source_snapshot"
    return {
        "status": status,
        "blocked_reason": blocked_reason,
        "source_model_path": sanitize(root),
        "file_count": len(files),
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "total_size_gb": round(sum(item["size_bytes"] for item in files) / (1024**3), 4),
        "symlink_count": symlink_count,
        "files": files,
        "skipped": skipped,
        "broken": broken,
    }


def public_manifest(manifest):
    public = dict(manifest)
    public["files"] = [{k: v for k, v in item.items() if k != "source_path_internal"} for item in manifest.get("files", [])]
    return public


def build_dest_manifest(dest, source_manifest):
    files = []
    for item in source_manifest.get("files", []):
        rel = item["relative_path"]
        path = dest / rel
        if not path.exists() or not path.is_file():
            continue
        suffix = path.suffix.lower()
        limit = SAFETENSORS_HASH_LIMIT if suffix == ".safetensors" else SMALL_HASH_LIMIT
        digest = sha256(path, max_bytes=limit)
        files.append(
            {
                "relative_path": rel,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "hash_status": "sha256" if digest else "size_only_for_large_file",
            }
        )
    return {
        "status": "ok",
        "blocked_reason": "none",
        "remote_model_path": "models/Qwen2.5-7B-Instruct",
        "file_count": len(files),
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "total_size_gb": round(sum(item["size_bytes"] for item in files) / (1024**3), 4),
        "files": files,
    }


def compare(source_manifest, dest_manifest):
    left = {item["relative_path"]: item for item in source_manifest.get("files", [])}
    right = {item["relative_path"]: item for item in dest_manifest.get("files", [])}
    missing = sorted(set(left) - set(right))
    extra = sorted(set(right) - set(left))
    mismatched = []
    for rel in sorted(set(left) & set(right)):
        src = left[rel]
        dst = right[rel]
        if src["size_bytes"] != dst["size_bytes"]:
            mismatched.append({"relative_path": rel, "reason": "size_mismatch"})
        elif src.get("sha256") and dst.get("sha256") and src["sha256"] != dst["sha256"]:
            mismatched.append({"relative_path": rel, "reason": "sha256_mismatch"})
    ok = not missing and not extra and not mismatched
    return {
        "status": "ok" if ok else "blocked",
        "blocked_reason": "none" if ok else "manifest_comparison_failed",
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
    }


def write_reports(report, source_manifest=None, dest_manifest=None):
    if source_manifest:
        (REPORT_DIR / "local_model_manifest.json").write_text(
            json.dumps(public_manifest(source_manifest), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if dest_manifest:
        (REPORT_DIR / "remote_model_manifest.json").write_text(
            json.dumps(dest_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    (REPORT_DIR / "qwen_model_upload_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    lines = [
        "# Qwen Model Upload Report",
        "",
        f"upload_status: {report['upload_status']}",
        f"blocked_reason: {report['blocked_reason']}",
        "source_kind: remote_existing_snapshot",
        f"selected_remote_model_path: {report.get('selected_remote_model_path', 'none')}",
        f"remote_model_path: {report.get('remote_model_path', 'models/Qwen2.5-7B-Instruct')}",
        f"file_count: {report.get('file_count', 0)}",
        f"total_size_gb: {report.get('total_size_gb', 0)}",
        f"copied_files: {len(report.get('copied_files', []))}",
        f"skipped_existing_files: {len(report.get('skipped_existing_files', []))}",
        f"failed_files: {len(report.get('failed_files', []))}",
        f"manifest_comparison_status: {report.get('manifest_comparison', {}).get('status', 'not_run')}",
    ]
    (REPORT_DIR / "qwen_model_upload_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_confirmed_search_report(validation, status, blocked_reason):
    selected = dict(validation)
    selected["selected"] = bool(validation.get("valid"))
    selected["source"] = "user_confirmed_remote_candidate"
    payload = {
        "status": status,
        "blocked_reason": blocked_reason,
        "selected": selected if selected.get("valid") else None,
        "valid_candidates": [selected] if selected.get("valid") else [],
        "invalid_candidates": [] if selected.get("valid") else [selected],
        "multiple_candidates_need_confirmation": False,
        "search_timed_out": False,
        "remote_qwen25_path_was_set": False,
        "user_confirmed_candidate": True,
    }
    (REPORT_DIR / "remote_qwen_model_candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    lines = [
        "# Remote Qwen Model Search Report",
        "",
        f"status: {status}",
        f"blocked_reason: {blocked_reason}",
        "user_confirmed_candidate: true",
        "multiple_candidates_need_confirmation: false",
        f"selected_model_path: {selected.get('path', 'none') if selected.get('valid') else 'none'}",
        f"selected_is_instruct: {str(selected.get('is_instruct', False)).lower()}",
        f"valid_candidate_count: {1 if selected.get('valid') else 0}",
        "",
        "## Selected Candidate",
    ]
    if selected.get("valid"):
        lines.append(f"- {selected.get('path')} source=user_confirmed_remote_candidate")
    (REPORT_DIR / "remote_qwen_model_search_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


raw_source = os.environ.get("REMOTE_QWEN_SOURCE", "")
source = Path(raw_source).expanduser().resolve()
validation = validate_candidate(source)
report = {
    "upload_status": "blocked",
    "blocked_reason": validation.get("blocked_reason", "source_validation_failed"),
    "source_kind": "remote_existing_snapshot",
    "selected_remote_model_path": sanitize(source),
    "remote_model_path": "models/Qwen2.5-7B-Instruct",
    "source_validation": validation,
}
if not validation.get("valid"):
    write_confirmed_search_report(validation, "blocked", validation.get("blocked_reason", "source_validation_failed"))
    write_reports(report)
    print(json.dumps({"upload_status": report["upload_status"], "blocked_reason": report["blocked_reason"]}, ensure_ascii=False))
    raise SystemExit(2)
write_confirmed_search_report(validation, "ok", "none")

source_manifest = build_source_manifest(source)
report.update(
    {
        "blocked_reason": source_manifest.get("blocked_reason", "none"),
        "file_count": source_manifest["file_count"],
        "total_size_gb": source_manifest["total_size_gb"],
        "source_manifest": public_manifest(source_manifest),
    }
)
if source_manifest["status"] != "ok":
    write_reports(report, source_manifest)
    print(json.dumps({"upload_status": report["upload_status"], "blocked_reason": report["blocked_reason"]}, ensure_ascii=False))
    raise SystemExit(2)

required = int(source_manifest["total_size_bytes"] * 1.2 + 10 * 1024**3)
free = shutil.disk_usage(".").free
disk = {
    "free_bytes": free,
    "required_bytes": required,
    "free_gb": round(free / (1024**3), 3),
    "required_gb": round(required / (1024**3), 3),
    "allowed": free >= required,
}
report["remote_disk_preflight"] = disk
if not disk["allowed"]:
    report["blocked_reason"] = "insufficient_remote_disk"
    write_reports(report, source_manifest)
    print(json.dumps({"upload_status": report["upload_status"], "blocked_reason": report["blocked_reason"], "free_gb": disk["free_gb"], "required_gb": disk["required_gb"]}, ensure_ascii=False))
    raise SystemExit(2)

DEST.mkdir(parents=True, exist_ok=True)
copied = []
skipped = []
failed = []
for item in source_manifest["files"]:
    rel = item["relative_path"]
    src = Path(item["source_path_internal"])
    dst = DEST / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists() and dst.stat().st_size == item["size_bytes"]:
            skipped.append(rel)
            continue
        tmp = dst.with_name(dst.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(src, tmp)
        tmp.replace(dst)
        copied.append(rel)
    except Exception as exc:
        failed.append({"relative_path": rel, "error_type": type(exc).__name__})

dest_manifest = build_dest_manifest(DEST, source_manifest)
comparison = compare(source_manifest, dest_manifest)
ok = not failed and comparison["status"] == "ok"
report.update(
    {
        "upload_status": "ok" if ok else "blocked",
        "blocked_reason": "none" if ok else (comparison.get("blocked_reason") or "remote_staging_failed"),
        "copied_files": copied,
        "skipped_existing_files": skipped,
        "failed_files": failed,
        "remote_manifest": dest_manifest,
        "manifest_comparison": comparison,
    }
)
write_reports(report, source_manifest, dest_manifest)
print(json.dumps({"upload_status": report["upload_status"], "blocked_reason": report["blocked_reason"], "copied_files": len(copied), "skipped_existing_files": len(skipped)}, ensure_ascii=False))
raise SystemExit(0 if ok else 2)
PY
"""


def _pull(runner: SSHRunner, name: str) -> None:
    runner.sftp_get_file(f"{runner.config.remote_dir}/data/outputs/reports/{name}", LOCAL_REPORTS / name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage an already-present remote Qwen2.5-7B-Instruct snapshot into ~/aibola/models without downloading.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = ServerConfig.from_project_root(args.root)
    runner = SSHRunner(cfg, timeout=1800, dry_run=args.dry_run)
    command = (
        remote_project_prelude(cfg, create=True)
        + f"REMOTE_QWEN_SOURCE={shlex.quote(args.source)} "
        + REMOTE_STAGE.lstrip()
    )
    result = runner.run(command, "logs/remote/stage_remote_qwen_sanitized.log")
    print(result.stdout or result.stderr)
    for name in [
        "qwen_model_upload_report.md",
        "qwen_model_upload_report.json",
        "local_model_manifest.json",
        "remote_model_manifest.json",
    ]:
        _pull(runner, name)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
