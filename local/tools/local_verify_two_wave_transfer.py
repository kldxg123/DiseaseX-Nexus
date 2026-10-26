from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

SENSITIVE_RE = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization|pgpassword)(\s*[=:]\s*)([^,\s\"']+)"
)
HEADER_SECRET_RE = re.compile(rb"(?im)^(\s*(?:set-cookie|cookie|authorization)\s*:\s*)(.+)$")
DSN_RE = re.compile(r"postgresql(?:\+\w+)?://[^\s\"']+", re.I)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_zip(zf: zipfile.ZipFile) -> dict:
    hits = []
    scanned = 0
    errors = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        scanned += 1
        try:
            data = zf.read(info.filename)
        except Exception as exc:  # noqa: BLE001 - validation receipt records exact error
            errors.append({"path": info.filename, "error": type(exc).__name__})
            continue
        text = data.decode("utf-8", errors="ignore")
        for match in SENSITIVE_RE.finditer(text):
            hits.append({"path": info.filename, "pattern": "secret_keyword", "start": match.start()})
        for match in DSN_RE.finditer(text):
            hits.append({"path": info.filename, "pattern": "dsn", "start": match.start()})
        for match in HEADER_SECRET_RE.finditer(data):
            hits.append({"path": info.filename, "pattern": "sensitive_header", "start": match.start()})
    return {
        "files_expected_to_scan": scanned,
        "files_scanned": scanned - len(errors),
        "scan_skipped_count": 0,
        "scan_error_count": len(errors),
        "secret_hit_count": len(hits),
        "hits": hits[:50],
        "errors": errors[:20],
    }


def zip_path_validation(zf: zipfile.ZipFile) -> dict:
    unsafe = []
    symlinks = []
    case_seen = {}
    case_collisions = []
    for info in zf.infolist():
        name = info.filename
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts:
            unsafe.append(name)
        mode = (info.external_attr >> 16) & 0o777777
        if (mode & 0o170000) == 0o120000:
            symlinks.append(name)
        lowered = name.lower()
        if lowered in case_seen and case_seen[lowered] != name:
            case_collisions.append([case_seen[lowered], name])
        case_seen[lowered] = name
    return {
        "unsafe_path_count": len(unsafe),
        "unsafe_symlink_count": len(symlinks),
        "case_collision_count": len(case_collisions),
        "unsafe_paths": unsafe[:20],
        "unsafe_symlinks": symlinks[:20],
        "case_collisions": case_collisions[:20],
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: local_verify_two_wave_transfer.py <root_execution_id>")
    root_execution_id = sys.argv[1]
    root = Path("F:/作业/aibola/reports") / root_execution_id
    packages = [
        f"{root_execution_id}_wave1_hanta_legionella",
        f"{root_execution_id}_wave2_nipah",
        f"{root_execution_id}_final_merged",
    ]
    receipts = []
    for package_id in packages:
        zip_path = root / "archives" / f"{package_id}.zip"
        sha_path = root / "archives" / f"{package_id}.zip.sha256"
        build_path = root / "archives" / f"{package_id}_remote_build_receipt.json"
        build = json.loads(build_path.read_text(encoding="utf-8"))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            first_bad = zf.testzip()
            path_validation = zip_path_validation(zf)
            secret_scan = scan_zip(zf)
        receipt = {
            "package_id": package_id,
            "local_report_dir": str(root),
            "local_zip_path": str(zip_path),
            "local_sha256_path": str(sha_path),
            "remote_build_receipt_path": str(build_path),
            "remote_build_receipt_sha256": sha256(build_path),
            "remote_zip_sha256": build.get("zip_sha256"),
            "local_zip_sha256": sha256(zip_path),
            "detached_sha256_text": sha_path.read_text(encoding="utf-8").strip(),
            "remote_local_sha_match": sha256(zip_path) == build.get("zip_sha256"),
            "zip_integrity": "PASS" if first_bad is None else "FAIL",
            "zip_entry_count": len(names),
            **path_validation,
            "secret_scan": secret_scan,
            "first_bad_member": first_bad,
            "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        out_path = root / "archives" / f"{package_id}_local_transfer_receipt.json"
        out_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        final_validation = {
            "package_id": package_id,
            "state": "PASS"
            if receipt["remote_local_sha_match"]
            and receipt["zip_integrity"] == "PASS"
            and receipt["unsafe_path_count"] == 0
            and receipt["unsafe_symlink_count"] == 0
            and secret_scan["secret_hit_count"] == 0
            and secret_scan["scan_error_count"] == 0
            and secret_scan["scan_skipped_count"] == 0
            else "FAILED",
            "remote_local_sha_match": receipt["remote_local_sha_match"],
            "zip_integrity": receipt["zip_integrity"],
            "unsafe_path_count": receipt["unsafe_path_count"],
            "unsafe_symlink_count": receipt["unsafe_symlink_count"],
            "secret_scan": secret_scan,
            "local_transfer_receipt_path": str(out_path),
            "verified_at_utc": receipt["verified_at_utc"],
        }
        final_path = root / "archives" / f"{package_id}_final_validation_receipt.json"
        final_path.write_text(json.dumps(final_validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt["final_validation_receipt_path"] = str(final_path)
        receipt["final_validation_state"] = final_validation["state"]
        receipts.append(receipt)
    aggregate = {
        "root_execution_id": root_execution_id,
        "state": "PASS" if all(r["final_validation_state"] == "PASS" for r in receipts) else "FAILED",
        "evidence_package_status": "COMPLETE_SAFE_ARCHIVE_VERIFIED"
        if all(r["final_validation_state"] == "PASS" for r in receipts)
        else "INCOMPLETE_OR_UNSAFE",
        "archives": receipts,
        "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    (root / "archives" / "FINAL_DELIVERY_AGGREGATE_RECEIPT.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipts, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
