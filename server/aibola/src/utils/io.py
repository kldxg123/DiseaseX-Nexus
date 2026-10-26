from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Iterator, Sequence


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_parent(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    records: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: str | Path, records: Iterable[dict]) -> Path:
    p = ensure_parent(path)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(to_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")
    return p


def write_json(path: str | Path, payload: dict | list) -> Path:
    p = ensure_parent(path)
    p.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return p


def to_jsonable(value):
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "tolist"):
        return to_jsonable(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def read_csv_records(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv_records(path: str | Path, records: Sequence[dict]) -> Path:
    p = ensure_parent(path)
    keys: list[str] = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(records)
    return p


def write_table(path: str | Path, records: Sequence[dict]) -> Path:
    """Write a table, falling back to JSONL when parquet dependencies are unavailable."""
    p = ensure_parent(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        return write_csv_records(p, records)
    if suffix == ".jsonl":
        return write_jsonl(p, records)
    if suffix == ".parquet":
        try:
            import pandas as pd  # type: ignore

            pd.DataFrame(list(records)).to_parquet(p, index=False)
            return p
        except Exception as exc:
            fallback = p.with_suffix(p.suffix + ".jsonl")
            write_jsonl(fallback, records)
            p.write_text(
                "Parquet writer unavailable; see sibling JSONL fallback.\n"
                f"fallback={fallback.name}\n"
                f"reason={type(exc).__name__}\n",
                encoding="utf-8",
            )
            return fallback
    return write_jsonl(p, records)


def read_table(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        fallback = p.with_suffix(p.suffix + ".jsonl")
        if fallback.exists():
            return read_jsonl(fallback)
        return []
    if p.suffix.lower() == ".csv":
        return read_csv_records(p)
    if p.suffix.lower() == ".jsonl":
        return read_jsonl(p)
    if p.suffix.lower() == ".parquet":
        try:
            import pandas as pd  # type: ignore

            return pd.read_parquet(p).to_dict(orient="records")
        except Exception:
            fallback = p.with_suffix(p.suffix + ".jsonl")
            return read_jsonl(fallback)
    return read_jsonl(p)


def iter_chunks(records: Sequence[dict], size: int) -> Iterator[list[dict]]:
    for idx in range(0, len(records), size):
        yield list(records[idx : idx + size])
