from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import r4e_r1_runner as r4e


def test_revision_fixture_keeps_a_b_a_chain() -> None:
    _inputs, chain, validation = r4e.revision_fixture()
    rows = chain["rows"]
    assert [row["value"] for row in rows] == [10.0, 20.0, 10.0]
    assert [row["revision"] for row in rows] == [1, 2, 3]
    assert rows[1]["supersedes"] == rows[0]["row_id"]
    assert rows[2]["supersedes"] == rows[1]["row_id"]
    assert validation["validation"]["current_row"]["revision"] == 3
    assert validation["validation"]["old_s1_replay_action"] == "NO_OP_ALREADY_PROCESSED"


def test_same_current_value_new_snapshot_noops() -> None:
    fixture = r4e.RevisionFixture()
    first = fixture.ingest("S1", "series", 5.0, 1)
    second = fixture.ingest("S2", "series", 5.0, 2)
    assert first["inserted"] == 1
    assert second["action"] == "NO_OP_CURRENT_VALUE_SAME"
    assert len(fixture.rows) == 1


def test_out_of_order_snapshot_fails_closed() -> None:
    fixture = r4e.RevisionFixture()
    assert fixture.ingest("S2", "series", 6.0, 2)["inserted"] == 1
    assert fixture.ingest("S1", "series", 7.0, 1)["action"] == "FAIL_OUT_OF_ORDER"
    assert len(fixture.rows) == 1


def test_metric_guard_blocks_weighted() -> None:
    payload = {
        "from_database": [
            {"metric_key": "percent_unweighted_ili", "n": 43998, "zero_n": 0, "nonzero_n": 43998},
            {"metric_key": "percent_weighted_ili", "n": 69, "zero_n": 69, "nonzero_n": 0},
        ]
    }

    class Cursor:
        description = [type("D", (), {"name": "location_name"})()]

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _sql: str, _args: tuple[object, ...] = ()) -> None:
            return None

        def fetchall(self) -> list[tuple[str]]:
            return [("Alabama",), ("New York City",)]

    class Conn:
        def cursor(self) -> Cursor:
            return Cursor()

    guard = r4e.metric_guard(payload, Conn())  # type: ignore[arg-type]
    assert guard["percent_unweighted_ili"]["future_r4f_use"] == "ALLOWED_AFTER_HUMAN_REVIEW"
    assert guard["percent_weighted_ili"]["future_r4f_use"] == "BLOCKED"
    assert guard["activity_level"]["future_r4f_use"] == "BLOCKED_NOT_TARGET"


def test_secret_scan_detects_private_key(tmp_path: Path) -> None:
    target = tmp_path / "bad.txt"
    target.write_text("-----BEGIN " + "OPENSSH PRIVATE " + "KEY-----\n", encoding="utf-8")
    result = r4e.secret_scan([tmp_path])
    assert result["status"] == "FAIL"


def test_safe_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../bad.txt", "x")
    result = r4e.safe_zip_audit(archive)
    assert result["safe"] is False


def test_manifest_hashes_member_bytes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    entries = r4e.make_manifest(tmp_path)
    assert entries[0]["sha256"] == r4e.sha256_path(tmp_path / "a.txt")
    manifest = (tmp_path / "MANIFEST.sha256").read_text(encoding="utf-8")
    assert "a.txt" in manifest


def test_validation_has_22_groups(tmp_path: Path) -> None:
    dummy_log = {
        "exit_code": 0,
        "redacted_command": "python -m tool",
        "stdout_path": "logs/stdout",
        "stderr_path": "logs/stderr",
    }
    checks = r4e.build_validation_checks(
        tmp_path,
        {"status": "PASS"},
        {"safe": True},
        {
            "schema_fingerprint": "x",
            "event_week_start_min": "2010-10-03",
            "latest_week_end": "2026-07-25",
            "duplicate_region_week_keys": 0,
        },
        {"status": "PASS"},
        {"same_raw_replay_committed_no_op": True},
        {"validation": {"status": "PASS"}},
        {"status": "PASS"},
        {"status": "PASS"},
        {"x": 1},
        {"x": 1},
        dummy_log,
        dummy_log,
        dummy_log,
        {"status": "PASS"},
        {"git_metadata": "NO_GIT_ROOT_AT_PROJECT_ROOT"},
    )
    assert len(checks) == 22
    assert {item["check_id"] for item in checks} >= {
        "V13_SAME_RAW_PER_TABLE_IDEMPOTENT_REPLAY",
        "V22_CHANGED_FILES_DIFF_CHECK_AND_PACKAGE_MANIFEST",
    }


def test_json_report_writer_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    r4e.write_json(path, {"b": 2, "a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}


def test_table_name_split() -> None:
    assert r4e.split_table("schema.table") == ("schema", "table")
    with pytest.raises(ValueError):
        r4e.split_table("missing_dot")
