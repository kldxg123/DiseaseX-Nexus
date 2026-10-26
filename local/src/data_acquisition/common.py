from __future__ import annotations

import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from src.utils.io import ensure_parent, write_json


REPORT_DIR = Path("data/outputs/reports")
RAW_ROOT = Path("data/raw/external_v3")
USER_AGENT = "aibola-p1b-data-acquisition/1.0 (research; public open data)"


@dataclass
class AcquisitionRecord:
    dataset_id: str
    source: str
    status: str
    url: str
    group: str = ""
    scope: str = ""
    role_hint: str = ""
    local_path: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    reason: str | None = None
    license: str | None = None
    expected_granularity: str | None = None
    metadata: dict[str, Any] | None = None


def load_yaml(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return json.loads(text)


def safe_name(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().lower()
    kept = [ch if ch.isalnum() else "_" for ch in text]
    name = "".join(kept).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    return (name or "item")[:limit]


def request_bytes(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def request_json(url: str, timeout: int = 90) -> Any:
    return json.loads(request_bytes(url, timeout=timeout).decode("utf-8", errors="replace"))


def head_size(url: str, timeout: int = 30) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length and length.isdigit() else None
    except Exception:
        return None


def file_sha256(path: str | Path) -> str:
    sha = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def download_file(url: str, path: Path, max_file_mb: int, timeout: int = 180) -> tuple[str, int | None, str | None, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(max_file_mb) * 1024 * 1024
    if path.exists() and path.stat().st_size > 0:
        return "downloaded", path.stat().st_size, file_sha256(path), "already_exists"
    expected = head_size(url)
    if expected and expected > max_bytes:
        return "skipped", expected, None, f"file_size_exceeds_limit_{max_file_mb}MB"
    tmp = path.with_suffix(path.suffix + ".tmp")
    sha = hashlib.sha256()
    total = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response, tmp.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    tmp.unlink(missing_ok=True)
                    return "skipped", total, None, f"file_size_exceeds_limit_{max_file_mb}MB"
                sha.update(chunk)
                handle.write(chunk)
        tmp.replace(path)
        return "downloaded", total, sha.hexdigest(), None
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return "failed", total or expected, None, f"{type(exc).__name__}: {str(exc)[:180]}"


def write_text_artifact(url: str, path: Path, max_file_mb: int, timeout: int = 90) -> tuple[str, int | None, str | None, str | None]:
    return download_file(url, path, max_file_mb=max_file_mb, timeout=timeout)


def write_report_pair(stem: str, title: str, payload: dict[str, Any], lines: list[str] | None = None) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(REPORT_DIR / f"{stem}.json", payload)
    if lines is None:
        lines = [
            f"# {title}",
            "",
            f"status: {payload.get('status', 'unknown')}",
            f"blocked_reason: {payload.get('blocked_reason', 'unknown')}",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    (REPORT_DIR / f"{stem}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def records_payload(records: Iterable[AcquisitionRecord], status: str = "ok", blocked_reason: str = "none") -> dict[str, Any]:
    rows = [asdict(record) for record in records]
    return {
        "status": status,
        "blocked_reason": blocked_reason,
        "raw_root": RAW_ROOT.as_posix(),
        "summary": {
            "total_records": len(rows),
            "downloaded": sum(1 for row in rows if row["status"] == "downloaded"),
            "failed": sum(1 for row in rows if row["status"] == "failed"),
            "skipped": sum(1 for row in rows if row["status"] == "skipped"),
            "metadata_only": sum(1 for row in rows if row["status"] == "metadata_only"),
            "downloaded_bytes": sum(int(row.get("bytes") or 0) for row in rows if row["status"] == "downloaded"),
        },
        "records": rows,
        "forbidden_actions_not_performed": ["training", "tmux", "model_download", "promotion", "external_test_threshold_tuning"],
    }


def report_records(stem: str, title: str, records: list[AcquisitionRecord], status: str = "ok", blocked_reason: str = "none") -> dict[str, Any]:
    payload = records_payload(records, status=status, blocked_reason=blocked_reason)
    lines = [
        f"# {title}",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"raw_root: `{payload['raw_root']}`",
        "",
        "## Summary",
        "",
        "| total | downloaded | failed | skipped | metadata_only | bytes |",
        "|---:|---:|---:|---:|---:|---:|",
        "| {total_records} | {downloaded} | {failed} | {skipped} | {metadata_only} | {downloaded_bytes} |".format(**payload["summary"]),
        "",
        "## Records",
        "",
        "| dataset_id | source | status | scope | path | reason |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload["records"]:
        lines.append(
            "| {dataset_id} | {source} | {status} | {scope} | `{path}` | {reason} |".format(
                dataset_id=row.get("dataset_id", ""),
                source=row.get("source", ""),
                status=row.get("status", ""),
                scope=row.get("scope", ""),
                path=row.get("local_path") or "",
                reason=str(row.get("reason") or "").replace("|", "/"),
            )
        )
    write_report_pair(stem, title, payload, lines)
    return payload


def append_manifest(records: list[AcquisitionRecord]) -> dict[str, Any]:
    manifest_path = RAW_ROOT / "p1b_download_manifest.json"
    existing: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    rows = existing.get("records", [])
    rows.extend(asdict(record) for record in records)
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("dataset_id") or row.get("url") or len(by_id))
        by_id[key] = row
    payload = {
        "raw_root": RAW_ROOT.as_posix(),
        "records": list(by_id.values()),
        "summary": {
            "total_records": len(by_id),
            "downloaded": sum(1 for row in by_id.values() if row.get("status") == "downloaded"),
            "failed": sum(1 for row in by_id.values() if row.get("status") == "failed"),
            "skipped": sum(1 for row in by_id.values() if row.get("status") == "skipped"),
            "metadata_only": sum(1 for row in by_id.values() if row.get("status") == "metadata_only"),
        },
    }
    write_json(manifest_path, payload)
    return payload


def source_items(registry: dict[str, Any], group: str | None = None) -> dict[str, dict[str, Any]]:
    sources = registry.get("sources", {})
    if not isinstance(sources, dict):
        return {}
    if group is None:
        return {str(k): v for k, v in sources.items() if isinstance(v, dict)}
    return {str(k): v for k, v in sources.items() if isinstance(v, dict) and v.get("group") == group}


def infer_scope(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ["uganda", "mubende", "kassanda"]):
        return "uganda_2022"
    if any(token in lower for token in ["drc", "rdc", "congo", "kivu", "ituri", "beni"]):
        return "drc_2018_2020"
    if "guinea" in lower and "2021" in lower:
        return "guinea_2021"
    if any(token in lower for token in ["sierra", "liberia", "west africa", "guinea"]):
        return "west_africa_2014_2016"
    return "unclassified"


def default_role_for_scope(scope: str, registry: dict[str, Any]) -> str:
    scope_info = registry.get("outbreak_scopes", {}).get(scope, {})
    return str(scope_info.get("target_role") or "cross_check_only")


def extension_from_url(url: str, fallback: str = ".dat") -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    return suffix if suffix else fallback


def read_manifest_records() -> list[dict[str, Any]]:
    path = RAW_ROOT / "p1b_download_manifest.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("records", [])
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def write_rows_csv(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    p = ensure_parent(path)
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with p.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return p


def polite_sleep(seconds: float = 0.15) -> None:
    time.sleep(seconds)
