from __future__ import annotations

import json

from scripts import check_source_access_policy


def test_check_source_access_policy_validates_example_policy(capsys) -> None:
    exit_code = check_source_access_policy.main(
        ["--policy", "configs/source_access_policy.example.json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["policies_checked"] == 6
    assert payload["errors"] == []
    assert payload["decision"] is None


def test_check_source_access_policy_decision_outputs_safe_json(capsys) -> None:
    exit_code = check_source_access_policy.main(
        [
            "--policy",
            "configs/source_access_policy.example.json",
            "--source-key",
            "cdc_norostat",
            "--url",
            "https://example.invalid/test?token=hidden",
            "--allow-network",
            "--terms-reviewed",
            "--manual-reviewed",
            "--robots-allowed",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["decision"]["allowed"] is True
    dumped = captured.out.lower()
    assert "hidden" not in dumped
    assert "token" not in dumped
    assert "password" not in dumped
    assert "secret" not in dumped


def test_check_source_access_policy_bad_file_returns_failure(tmp_path, capsys) -> None:
    bad_policy = tmp_path / "bad-policy.json"
    bad_policy.write_text('{"sources": [{"source_key": "bad"}]}', encoding="utf-8")

    exit_code = check_source_access_policy.main(["--policy", str(bad_policy)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["policies_checked"] == 0
    assert payload["errors"]
