from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/runtime/fresh_source")
RAW = ROOT / "who_fluid_frozen_20260814.csv"
OUT = ROOT / "who_fluid_lao_ili_rate_frozen.csv"
META = ROOT / "who_fluid_lao_ili_rate_frozen.metadata.json"
RAW_SHA = "99fe45c41db35bcede7153308c4a4a3ed43369909709618bbe9bd34af567e165"


def number(text: str | None) -> float | None:
    value = (text or "").strip()
    if value in {"", "NA", "N/A", "-"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def period(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def main() -> None:
    if OUT.exists() or META.exists():
        raise SystemExit("OUTPUT_ALREADY_EXISTS")
    if hashlib.sha256(RAW.read_bytes()).hexdigest() != RAW_SHA:
        raise SystemExit("RAW_SHA256_MISMATCH")
    observed: dict[date, list[float]] = {}
    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("COUNTRY_CODE") or "").strip() != "LAO":
                continue
            if (row.get("AGEGROUP_CODE") or "").strip().casefold() != "all":
                continue
            current = period(row.get("ISO_WEEKSTARTDATE"))
            cases = number(row.get("ILI_CASE"))
            outpatients = number(row.get("ILI_OUTPATIENTS"))
            if current is None or cases is None or cases < 0 or outpatients is None or outpatients <= 0:
                continue
            observed.setdefault(current, []).append(1000.0 * cases / outpatients)
    conflicts = {week: values for week, values in observed.items() if max(values) - min(values) > 1e-12}
    if conflicts:
        raise SystemExit(f"DUPLICATE_CONFLICT_WEEKS:{len(conflicts)}")
    first, last = min(observed), max(observed)
    rows: list[dict[str, str]] = []
    current = first
    while current <= last:
        values = observed.get(current)
        value = "" if not values else format(values[0], ".12g")
        row_sha = hashlib.sha256(f"{current.isoformat()}|{value}|{RAW_SHA}".encode()).hexdigest()
        rows.append({"period_start": current.isoformat(), "value": value, "row_sha256": row_sha})
        current += timedelta(weeks=1)
    fd, temp_name = tempfile.mkstemp(prefix="lao_ili_", suffix=".part", dir=ROOT)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["period_start", "value", "row_sha256"])
            writer.writeheader()
            writer.writerows(rows)
        canonical_sha = hashlib.sha256(temp.read_bytes()).hexdigest()
        os.replace(temp, OUT)
    finally:
        if temp.exists():
            temp.unlink()
    hidden = rows[-52:]
    payload = {
        "publisher": "World Health Organization",
        "dataset": "fluID",
        "country_code": "LAO",
        "country_name": "Lao People's Democratic Republic",
        "disease": "influenza",
        "metric": "who_fluid_ili_rate_per_1000_outpatients",
        "unit": "per_1000_outpatients",
        "source_raw_sha256": RAW_SHA,
        "canonical_file_sha256": canonical_sha,
        "calendar_row_count": len(rows),
        "observed_row_count": sum(bool(row["value"]) for row in rows),
        "missing_row_count": sum(not row["value"] for row in rows),
        "first_period": rows[0]["period_start"],
        "last_period": rows[-1]["period_start"],
        "hidden_start": hidden[0]["period_start"],
        "hidden_end": hidden[-1]["period_start"],
        "hidden_calendar_rows": len(hidden),
        "hidden_observed_rows": sum(bool(row["value"]) for row in hidden),
        "hidden_values_emitted": False,
        "missing_imputation": "NONE",
    }
    META.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
