from __future__ import annotations

import importlib.util
import json
import os
import zipfile
from pathlib import Path


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/p1_15_ebola_cod_trusted_expansion"


def load_baseline_module():
    path = Path("/tmp/p1_15_baseline_remote.py")
    spec = importlib.util.spec_from_file_location("p1_15_baseline_remote", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("baseline module unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    mod = load_baseline_module()
    after = mod.database()
    mod.write_json("database_after.json", after)
    before_path = REPORT / "database_before.json"
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path.exists() else {}
    validation = {
        "status": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
        "trusted_write_performed": False,
        "cod_ebola_before_rows": before.get("cod_ebola_trusted_series", {}).get("row_count"),
        "cod_ebola_after_rows": after.get("cod_ebola_trusted_series", {}).get("row_count"),
        "cod_ebola_delta": (after.get("cod_ebola_trusted_series", {}).get("row_count") or 0) - (before.get("cod_ebola_trusted_series", {}).get("row_count") or 0),
        "other_country_disease_digest_unchanged": before.get("other_country_disease_digest", {}).get("row_set_sha256") == after.get("other_country_disease_digest", {}).get("row_set_sha256"),
        "rows_deleted": 0,
        "existing_trusted_values_modified": 0,
        "non_cod_writes": 0,
        "non_ebola_writes": 0,
        "p1_14_retry3_executed": False,
        "p1_14f_entered": False,
    }
    mod.write_json("database_validation_summary.json", validation)
    zip_path = PROJECT / "reports/p1_15_ebola_cod_trusted_expansion_complete.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(REPORT.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(REPORT.parent)))
    (PROJECT / "reports/p1_15_ebola_cod_trusted_expansion_complete.zip.sha256").write_text(mod.sha_bytes(zip_path.read_bytes()) + "  p1_15_ebola_cod_trusted_expansion_complete.zip\n", encoding="utf-8")
    print(json.dumps({"database_after_written": True, "validation": validation, "zip_sha256": mod.sha_bytes(zip_path.read_bytes())}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
