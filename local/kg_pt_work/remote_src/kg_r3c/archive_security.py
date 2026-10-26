"""Archive security checks and safe staged extraction for KG import runs.

Read-only against the source archive: members are hashed, classified by magic
bytes, checked against traversal/link/bomb rules, and extracted only into a
task-owned staging directory. Nothing is written next to the source archive.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
import zipfile
from dataclasses import dataclass, field, asdict

MAX_MEMBER_BYTES = 2 * 1024**3          # single member cap (2 GiB)
MAX_TOTAL_BYTES = 20 * 1024**3          # whole archive cap (20 GiB)
MAX_RATIO = 100.0                       # uncompressed/compressed cap
MAX_NESTING_DEPTH = 1                   # zip inside zip allowed once
ALLOWED_KINDS = {"csv", "pdf", "zip"}

ARCHIVE_SHA256 = "64778b3070215d9fc08311c1e8303270565ebac8a267675f874dcdb21764dbe7"


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def detect_kind(name: str, head: bytes) -> str:
    """Classify by magic bytes first, extension second."""
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    # text-ish: no NUL in first 8KB, decodable in some encoding
    if b"\x00" not in head[:8192]:
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                head.decode(enc)
                return "csv" if name.lower().endswith(".csv") else "text"
            except (UnicodeDecodeError, UnicodeError):
                continue
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    return ext or "unknown"


def member_security_issues(info: zipfile.ZipInfo) -> list[str]:
    issues = []
    name = info.filename
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        issues.append("ABSOLUTE_PATH")
    parts = name.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        issues.append("TRAVERSAL_DOTDOT")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        issues.append("SYMLINK")
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
        issues.append("DEVICE_OR_FIFO")
    if info.flag_bits & 0x1:
        issues.append("ENCRYPTED")
    if info.file_size > MAX_MEMBER_BYTES:
        issues.append("MEMBER_TOO_LARGE")
    if info.compress_size > 0 and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
        issues.append("SUSPICIOUS_COMPRESSION_RATIO")
    return issues


@dataclass
class MemberRecord:
    archive_sha256: str
    member_path: str           # outer path, e.g. "第7-1.zip"
    inner_path: str = ""       # path inside nested zip ("" for level-1)
    depth: int = 1
    member_sha256: str = ""
    size: int = 0
    compress_size: int = 0
    crc: str = ""
    kind: str = ""
    mime: str = ""
    issues: list = field(default_factory=list)
    quarantined: bool = False

    @property
    def full_path(self):
        return self.member_path if not self.inner_path else f"{self.member_path}!{self.inner_path}"


MIME = {"pdf": "application/pdf", "zip": "application/zip", "csv": "text/csv", "text": "text/plain"}


def scan_zip_object(zf: zipfile.ZipFile, archive_sha256: str, outer: str, depth: int,
                    records: list[MemberRecord], hash_members: bool = True):
    """Record-level scan of one zip object. Does not extract."""
    for info in zf.infolist():
        if info.is_dir():
            continue
        rec = MemberRecord(
            archive_sha256=archive_sha256,
            member_path=outer or info.filename,
            inner_path=info.filename if outer else "",
            depth=depth,
            size=info.file_size,
            compress_size=info.compress_size,
            crc=format(info.CRC, "08x"),
        )
        rec.issues = member_security_issues(info)
        head = b""
        data = None
        if hash_members and not any(i in ("ENCRYPTED",) for i in rec.issues):
            try:
                with zf.open(info) as f:
                    data = f.read()
                rec.member_sha256 = hashlib.sha256(data).hexdigest()
                head = data[:8192]
                # CRC check
                import zlib
                if zlib.crc32(data) & 0xFFFFFFFF != info.CRC:
                    rec.issues.append("CRC_MISMATCH")
            except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as e:
                rec.issues.append(f"READ_ERROR:{type(e).__name__}")
                data = None
        rec.kind = detect_kind(info.filename, head)
        rec.mime = MIME.get(rec.kind, "application/octet-stream")
        # nested zip policy
        if rec.kind == "zip":
            if depth > MAX_NESTING_DEPTH:
                rec.issues.append("NESTING_TOO_DEEP")
        elif rec.kind not in ALLOWED_KINDS:
            rec.issues.append(f"UNEXPECTED_TYPE:{rec.kind}")
        rec.quarantined = bool(rec.issues)
        records.append(rec)
        # recurse into nested zip
        if rec.kind == "zip" and data is not None and depth <= MAX_NESTING_DEPTH and "CRC_MISMATCH" not in rec.issues:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as nested:
                    scan_zip_object(nested, archive_sha256, info.filename, depth + 1, records, hash_members)
            except zipfile.BadZipFile:
                rec.issues.append("NESTED_ZIP_UNREADABLE")
                rec.quarantined = True
    return records


def write_manifest_csv(records, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["archive_sha256", "member_path", "inner_path", "depth", "member_sha256",
                    "size", "compress_size", "crc", "kind", "mime", "issues", "quarantined"])
        for r in records:
            w.writerow([r.archive_sha256, r.member_path, r.inner_path, r.depth, r.member_sha256,
                        r.size, r.compress_size, r.crc, r.kind, r.mime,
                        ";".join(r.issues), int(r.quarantined)])


def security_report(archive_path, archive_sha256, records):
    total = sum(r.size for r in records)
    by_kind = {}
    for r in records:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    return {
        "archive_path": archive_path,
        "archive_sha256": archive_sha256,
        "archive_size": os.path.getsize(archive_path),
        "level1_member_count": sum(1 for r in records if r.depth == 1),
        "total_member_count_incl_nested": len(records),
        "by_kind": by_kind,
        "total_uncompressed_bytes": total,
        "quarantined_count": sum(1 for r in records if r.quarantined),
        "issue_histogram": {i: sum(1 for r in records for x in r.issues if x == i)
                            for i in sorted({x for r in records for x in r.issues})},
        "limits": {"max_member_bytes": MAX_MEMBER_BYTES, "max_total_bytes": MAX_TOTAL_BYTES,
                   "max_ratio": MAX_RATIO, "max_nesting_depth": MAX_NESTING_DEPTH},
        "archive_level_pass": total <= MAX_TOTAL_BYTES,
    }
