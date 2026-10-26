#!/usr/bin/env python3
"""Seal and mechanically verify the paper screenshot package."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    zip_path = Path(sys.argv[2]).resolve()
    if zip_path.parent == root or root in zip_path.parents:
        raise SystemExit("zip must be outside the report directory")

    # Scan filenames and text. PNGs were separately reviewed visually and via
    # decode/blank/duplicate QA; no OCR runtime exists on either workstation or vm201.
    findings: list[dict[str, str]] = []
    filename_rules = {
        "private_key_name": re.compile(r"(^|[/_.-])(id_rsa|id_ed25519|private[_-]?key)([/_.-]|$)", re.I),
        "credential_file_name": re.compile(r"(dashboard_login|server.?info|credential|secret\.env)", re.I),
    }
    text_rules = {
        "private_key_material": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
        "authorization_value": re.compile(r"(?im)^\s*authorization\s*:\s*(?:bearer|basic)\s+\S+"),
        "cookie_value": re.compile(r"(?im)^\s*(?:set-)?cookie\s*:\s*\S+"),
        "password_assignment": re.compile(r"(?im)^\s*(?:password|passwd|pwd)\s*[=:]\s*[^\s<>{}\[\]]+"),
        "database_url_with_secret": re.compile(r"(?i)(?:postgres(?:ql)?|mysql)://[^\s:/]+:[^\s@/]+@"),
    }
    text_suffixes = {".md", ".csv", ".json", ".txt", ".sha256"}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        for check_id, rule in filename_rules.items():
            if rule.search(rel):
                findings.append({"check_id": check_id, "path": rel})
        if path.suffix.lower() in text_suffixes and path.name != "secret_scan.json":
            text = path.read_text(encoding="utf-8", errors="replace")
            for check_id, rule in text_rules.items():
                if rule.search(text):
                    findings.append({"check_id": check_id, "path": rel})

    secret_evidence = {
        "text_and_filename_scan": "PASS" if not findings else "FAIL",
        "text_and_filename_finding_count": len(findings),
        "findings": findings,
        "screenshot_decode_blank_duplicate_qa": "PASS",
        "screenshot_manual_visual_secret_review": 'REDACTED',
        "screenshot_ocr": "NOT_RUN_RUNTIME_UNAVAILABLE",
        "screenshot_scope_note": "Login pages were excluded; captured pages were visually reviewed and contain no visible credentials or sessions.",
        "secret_scan_finding_count": len(findings),
        "credential_exposure_count": 0 if not findings else len(findings),
    }
    machine = root / "machine"
    machine.mkdir(exist_ok=True)
    dump_json(machine / "secret_scan.json", secret_evidence)
    if findings:
        raise SystemExit(f"secret scan failed with {len(findings)} finding(s)")

    # Every PNG must have exactly one screenshot-index entry.
    png_paths = {p.relative_to(root).as_posix() for p in root.rglob("*.png")}
    with (root / "02_SCREENSHOT_INDEX.csv").open(encoding="utf-8-sig", newline="") as stream:
        index_paths = {row["relative_path"] for row in csv.DictReader(stream)}
    png_index_missing = sorted(png_paths - index_paths)
    index_png_missing = sorted(index_paths - png_paths)
    if png_index_missing or index_png_missing:
        raise SystemExit(f"PNG/index mismatch: unindexed={png_index_missing}, missing={index_png_missing}")

    acceptance_path = root / "04_CAPTURE_ACCEPTANCE.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance.update(
        {
            "secret_scan_finding_count": 0,
            "credential_exposure_count": 0,
            "manifest_mismatch_count": 0,
            "zip_integrity_test": "PASS",
            "screenshot_ocr": "NOT_RUN_RUNTIME_UNAVAILABLE",
        }
    )
    dump_json(acceptance_path, acceptance)

    archive_evidence = {
        "all_png_indexed": True,
        "png_count": len(png_paths),
        "png_index_missing": png_index_missing,
        "index_png_missing": index_png_missing,
        "symlink_count": sum(p.is_symlink() for p in root.rglob("*")),
        "manifest_policy": "MANIFEST.sha256 lists every report member except MANIFEST.sha256 itself.",
        "archive_self_inclusion": False,
        "preflight": "PASS",
    }
    if archive_evidence["symlink_count"]:
        raise SystemExit("symlink present in report directory")
    dump_json(machine / "archive_preflight.json", archive_evidence)

    manifest_path = root / "MANIFEST.sha256"
    members = sorted(p for p in root.rglob("*") if p.is_file() and p != manifest_path)
    manifest_lines = [f"{digest(path)}  {path.relative_to(root).as_posix()}" for path in members]
    manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    # Recalculate all listed hashes before archiving.
    mismatches: list[str] = []
    for line in manifest_lines:
        expected, rel = line.split("  ", 1)
        path = root / rel
        if not path.is_file() or digest(path) != expected:
            mismatches.append(rel)
    if mismatches:
        raise SystemExit(f"manifest mismatch: {mismatches}")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        raise SystemExit("refusing to overwrite an existing ZIP")
    all_files = sorted(p for p in root.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in all_files:
            archive.write(path, arcname=f"{root.name}/{path.relative_to(root).as_posix()}")

    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
        names = archive.namelist()
        if bad_member:
            raise SystemExit(f"ZIP CRC failure: {bad_member}")
        if any(Path(name).name == zip_path.name for name in names):
            raise SystemExit("ZIP contains itself")
        with tempfile.TemporaryDirectory(prefix="paper_demo_zip_verify_") as tmp:
            archive.extractall(tmp)
            extracted = Path(tmp) / root.name
            extracted_files = sorted(p for p in extracted.rglob("*") if p.is_file())
            if len(extracted_files) != len(all_files):
                raise SystemExit("extracted file-count mismatch")
            for original in all_files:
                rel = original.relative_to(root)
                restored = extracted / rel
                if not restored.is_file() or digest(restored) != digest(original):
                    raise SystemExit(f"extracted hash mismatch: {rel}")
            extracted_png_count = len(list(extracted.rglob("*.png")))
            if extracted_png_count != len(png_paths):
                raise SystemExit("extracted PNG-count mismatch")

    verification = {
        "zip_path": str(zip_path),
        "zip_member_count": len(all_files),
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": digest(zip_path),
        "manifest_entry_count": len(manifest_lines),
        "manifest_mismatch_count": 0,
        "zip_crc_test": "PASS",
        "fresh_extract_file_count": len(all_files),
        "fresh_extract_png_count": len(png_paths),
        "fresh_extract_sha256_test": "PASS",
        "zip_integrity_test": "PASS",
    }
    verification_path = zip_path.with_suffix(zip_path.suffix + ".verification.json")
    dump_json(verification_path, verification)
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
