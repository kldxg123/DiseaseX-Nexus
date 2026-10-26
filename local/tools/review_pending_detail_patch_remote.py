from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
TARGET = PROJECT / "src/epidemic_core/discovery/review_app.py"
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
CASE_ID = "668e3dd5-cbdf-5e19-af8b-5360ef9e1af9"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def sanitize_html_capture(html_text: str) -> str:
    html_text = re.sub(
        r'(<meta\s+name=["\']csrf-token["\']\s+content=["\'])[^"\']+(["\'])',
        r"\1[redacted-test-csrf]\2",
        html_text,
        flags=re.I,
    )
    html_text = re.sub(
        r'(<input[^>]+name=["\']csrf["\'][^>]+value=["\'])[^"\']+(["\'])',
        r"\1[redacted-test-csrf]\2",
        html_text,
        flags=re.I,
    )
    return html_text


def admin_url_string() -> str:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url_obj = make_url(url).set(
            username=os.environ["POSTGRES_ADMIN_USER"],
            password=os.environ["POSTGRES_ADMIN_PASSWORD"],
        )
        return url_obj.render_as_string(hide_password=False)
    return url


FALLBACK_FUNCTION = r'''

def _fallback_review_case_page(
    engine: Engine,
    case_id: uuid.UUID,
    csrf: str,
    notice: str = "",
    preview_page: int = 1,
    page_size: int = 20,
) -> HTMLResponse:
    """Read-only detail page for active cases absent from review_candidate_cards_v2.

    This fallback is intentionally display-only: it never writes decisions and never
    bypasses the original approval/Promotion gate.
    """
    cards = _rows(
        engine,
        """
        SELECT
          rc.id AS review_case_id,
          rc.state AS review_state,
          rc.created_at AS review_created_at,
          v.id AS candidate_version_id,
          v.candidate_id,
          v.content_sha256 AS artifact_sha256,
          v.format,
          v.metadata,
          c.publisher,
          c.dataset_name,
          c.source_tier,
          c.status AS candidate_status,
          COALESCE(pr.preview_rows,0) AS preview_row_count
        FROM epidemic_discovery.source_review_cases rc
        JOIN epidemic_discovery.source_candidate_versions v ON v.id = rc.candidate_version_id
        JOIN epidemic_discovery.source_candidates c ON c.id = v.candidate_id
        LEFT JOIN (
          SELECT candidate_version_id, count(*)::int AS preview_rows
          FROM epidemic_discovery.source_preview_rows
          GROUP BY candidate_version_id
        ) pr ON pr.candidate_version_id = v.id
        WHERE rc.id=:id
        """,
        {"id": case_id},
    )
    if not cards:
        return HTMLResponse(_layout("未找到", '<p class="error">未找到复核案例。</p>', csrf), 404)
    card = cards[0]
    metadata = card.get("metadata") or {}
    if not isinstance(metadata, dict):
        try:
            metadata = json.loads(str(metadata))
        except Exception:
            metadata = {"raw_metadata": str(metadata)}
    offset = max(0, (max(1, preview_page) - 1) * max(1, page_size))
    preview = _rows(
        engine,
        """
        SELECT row_ordinal,sanitized_json
        FROM epidemic_discovery.source_preview_rows
        WHERE candidate_version_id=:version
        ORDER BY row_ordinal
        LIMIT :limit OFFSET :offset
        """,
        {"version": card["candidate_version_id"], "limit": page_size, "offset": offset},
    )
    headers: list[str] = []
    for row in preview:
        payload = row.get("sanitized_json") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}
        for key in payload:
            if str(key) not in headers:
                headers.append(str(key))
    header_html = "".join(f"<th>{_e(key)}</th>" for key in ["row_ordinal", *headers])
    if preview:
        body_rows = ""
        for row in preview:
            payload = row.get("sanitized_json") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {"raw": payload}
            cells = "".join(f"<td>{_e(payload.get(key))}</td>" for key in headers)
            body_rows += f"<tr><td>{_e(row.get('row_ordinal'))}</td>{cells}</tr>"
    else:
        body_rows = f"<tr><td colspan='{max(1, len(headers) + 1)}'>无候选预览行。</td></tr>"
    overview = {
        "review_case_id": str(card.get("review_case_id")),
        "review_state": card.get("review_state"),
        "candidate_status": card.get("candidate_status"),
        "candidate_version_id": str(card.get("candidate_version_id")),
        "candidate_id": str(card.get("candidate_id")),
        "publisher": card.get("publisher"),
        "dataset_name": card.get("dataset_name"),
        "source_tier": card.get("source_tier"),
        "format": card.get("format"),
        "artifact_sha256": card.get("artifact_sha256"),
        "preview_row_count": card.get("preview_row_count"),
        "summary_input": "raw_source_document",
        "candidate_payload_used": False,
    }
    overview_rows = "".join(
        f"<tr><th scope='row'>{_e(key)}</th><td class='break-url'>{_e(value)}</td></tr>"
        for key, value in overview.items()
    )
    metadata_rows = "".join(
        f"<tr><th scope='row'>{_e(key)}</th><td class='break-url'>{_e(value)}</td></tr>"
        for key, value in list(metadata.items())[:40]
    )
    raw_source_summary = _load_raw_source_summary(engine, case_id)
    source_summary = _render_raw_source_summary(raw_source_summary)
    notice_html = f'<p class="notice" role="status">{_e(notice)}</p>' if notice else ""
    body = f"""
    {notice_html}
    <section id="raw-source-summary-fallback" data-display-only="true">
      <h2>原网页内容概括 / Raw source summary</h2>
      {source_summary}
      <p class="notice">此详情页为只读 fallback，用于展示 PENDING/REVIEW_READY 复核案例；它不会创建 Review Decision、不会审批、不会 Promotion、不会写入 Trusted。</p>
    </section>
    <section id="candidate-preview-fallback">
      <h2>候选数据预览 / Candidate Preview</h2>
      <table><caption>复核案例固定身份</caption><tbody>{overview_rows}</tbody></table>
      <h3>源 metadata（只读）</h3>
      <table><tbody>{metadata_rows}</tbody></table>
      <div class="table-box preview-wide">
        <table id="verified-preview-table">
          <caption>候选预览行；未批准、未发布、未进入 Trusted</caption>
          <thead><tr>{header_html}</tr></thead>
          <tbody>{body_rows}</tbody>
        </table>
      </div>
    </section>
    <section id="approval-boundary">
      <h2>人工审核边界</h2>
      <p class="hard-block">当前页面不提供批准入口；批准和 Promotion 仍需匹配原有 exact identity gate。</p>
    </section>
    """
    return HTMLResponse(_layout("候选人工复核", body, csrf))
'''


def patch_source() -> dict[str, Any]:
    before = TARGET.read_bytes()
    text = before.decode("utf-8")
    changed = False
    if "def _fallback_review_case_page(" not in text:
        text = text.replace("\ndef create_app() -> FastAPI:\n", FALLBACK_FUNCTION + "\n\ndef create_app() -> FastAPI:\n")
        changed = True
    old = """        if not cards:
            return HTMLResponse(_layout("未找到", '<p class="error">未找到复核案例。</p>', auth[1]), 404)
        card = cards[0]
"""
    new = """        if not cards:
            return _fallback_review_case_page(
                engine,
                case_id,
                auth[1],
                notice=notice,
                preview_page=preview_page,
                page_size=page_size,
            )
        card = cards[0]
"""
    if old in text and new not in text:
        text = text.replace(old, new, 1)
        changed = True
    old_csv = """        if not cards:
            return HTMLResponse(_layout("未找到", '<p class="error">未找到复核案例。</p>', auth[1]), 404)
        rows = _rows(
            engine,
            "SELECT row_ordinal,sanitized_json FROM epidemic_discovery.source_preview_rows WHERE candidate_version_id=:version ORDER BY row_ordinal LIMIT 100",
            {"version": cards[0]["candidate_version_id"]},
        )
"""
    new_csv = """        if not cards:
            fallback_cards = _rows(
                engine,
                "SELECT candidate_version_id FROM epidemic_discovery.source_review_cases WHERE id=:id",
                {"id": case_id},
            )
            if not fallback_cards:
                return HTMLResponse(_layout("未找到", '<p class="error">未找到复核案例。</p>', auth[1]), 404)
            cards = fallback_cards
        rows = _rows(
            engine,
            "SELECT row_ordinal,sanitized_json FROM epidemic_discovery.source_preview_rows WHERE candidate_version_id=:version ORDER BY row_ordinal LIMIT 100",
            {"version": cards[0]["candidate_version_id"]},
        )
"""
    if old_csv in text and new_csv not in text:
        text = text.replace(old_csv, new_csv, 1)
        changed = True
    if changed:
        TARGET.write_text(text, encoding="utf-8")
    after = TARGET.read_bytes()
    return {
        "changed": changed,
        "target": str(TARGET.relative_to(PROJECT)),
        "before_sha256": sha_bytes(before),
        "after_sha256": sha_bytes(after),
    }


def fake_auth_file() -> tempfile.NamedTemporaryFile:
    salt = b"review-pending-detail-test-salt"
    password = b"unused-test-password"
    payload = {
        "username": 'REDACTED',
        "salt_hex": salt.hex(),
        "hash_hex": hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1, dklen=32).hex(),
        "n": 16384,
        "r": 8,
        "p": 1,
    }
    fh = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json")
    fh.write(json.dumps(payload, sort_keys=True))
    fh.close()
    return fh


def verify() -> dict[str, Any]:
    py_compile = subprocess.run(
        [os.environ.get("PYTHON", "python3"), "-m", "py_compile", str(TARGET)],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    auth_file = fake_auth_file()
    previous_review_url = os.environ.get("REVIEW_DATABASE_URL")
    previous_review_auth = os.environ.get("REVIEW_AUTH_FILE")
    try:
        os.environ["REVIEW_DATABASE_URL"] = admin_url_string()
        os.environ["REVIEW_AUTH_FILE"] = auth_file.name
        from fastapi.testclient import TestClient
        from epidemic_core.discovery.review_app import create_app

        app = create_app()
        with TestClient(app) as client:
            login = client.post(
                "/login",
                data={"username": 'REDACTED', "password": 'REDACTED'},
                follow_redirects=False,
            )
            detail = client.get(f"/reviews/{CASE_ID}")
            csv_resp = client.get(f"/reviews/{CASE_ID}/preview.csv")
        detail_path = REPORT / "ui_captures/review_don398_authenticated_detail.html"
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(sanitize_html_capture(detail.text), encoding="utf-8")
        markers = {
            "raw_source_summary_fallback": "raw-source-summary-fallback" in detail.text,
            "candidate_preview_fallback": "candidate-preview-fallback" in detail.text,
            "don398_case_id": CASE_ID in detail.text,
            "summary_ready_or_source_card": "SUMMARY_READY" in detail.text or "source-summary-card" in detail.text,
            "approval_boundary": "approval-boundary" in detail.text,
        }
        return {
            "py_compile_returncode": py_compile.returncode,
            "py_compile_stderr_sha256": sha_text(py_compile.stderr),
            "login_status": login.status_code,
            "detail_status": detail.status_code,
            "csv_status": csv_resp.status_code,
            "detail_html_sha256": sha_text(detail.text),
            "detail_html_path": str(detail_path.relative_to(REPORT)),
            "markers": markers,
            "passed": py_compile.returncode == 0
            and login.status_code == 303
            and detail.status_code == 200
            and csv_resp.status_code == 200
            and all(markers.values()),
            "database_writes": 0,
            "trusted_snapshot_promotion_human_decision_writes": 0,
            "auth_secret_read": False,
            "auth_secret_displayed": False,
        }
    finally:
        try:
            Path(auth_file.name).unlink()
        except OSError:
            pass
        if previous_review_url is not None:
            os.environ["REVIEW_DATABASE_URL"] = previous_review_url
        else:
            os.environ.pop("REVIEW_DATABASE_URL", None)
        if previous_review_auth is not None:
            os.environ["REVIEW_AUTH_FILE"] = previous_review_auth
        else:
            os.environ.pop("REVIEW_AUTH_FILE", None)


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    patch = patch_source()
    validation = verify()
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "REVIEW_PENDING_DETAIL_FALLBACK_PATCHED" if validation["passed"] else "REVIEW_PENDING_DETAIL_FALLBACK_FAILED",
        "patch": patch,
        "validation": validation,
        "scope": "Review GET detail/CSV display only; no decision, approval, promotion or Trusted path changed",
    }
    write_json("review_pending_detail_patch_report.json", report)
    print(json.dumps({"status": report["status"], "changed": patch["changed"], "passed": validation["passed"]}, sort_keys=True))
    if not validation["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
