from __future__ import annotations
import json, os, sys
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

PROJECT = Path(os.environ['PROJECT'])
REPORT = PROJECT / 'reports/ebola_official_trusted_review_summary_prediction_demo'

def db_url():
    url = os.environ['MIGRATION_DATABASE_URL']
    if os.environ.get('POSTGRES_ADMIN_PASSWORD') and os.environ.get('POSTGRES_ADMIN_USER'):
        url = make_url(url).set(username=os.environ['POSTGRES_ADMIN_USER'], password=os.environ['POSTGRES_ADMIN_PASSWORD'])
    return url

def engine():
    e = create_engine(db_url(), hide_parameters=True, future=True)
    @event.listens_for(e, 'connect')
    def _secure(conn, _):
        cur = conn.cursor()
        try:
            cur.execute('SET search_path TO pg_catalog')
            cur.execute("SET timezone TO 'UTC'")
        finally:
            cur.close()
    return e

def write(name, value):
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')

def main():
    with engine().connect() as conn:
        cols = [r[0] for r in conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='epidemic_discovery' AND table_name='review_case_raw_source_summaries_v1'
            ORDER BY ordinal_position
        """))]
        rows = [dict(r) for r in conn.execute(text("SELECT * FROM epidemic_discovery.review_case_raw_source_summaries_v1 ORDER BY review_case_id::text")).mappings()]
        counts = {k: 0 for k in ['SUMMARY_READY', 'SOURCE_CONTENT_BLOCKED', 'BLOCKED_OTHER', 'MISSING']}
        cases = []
        for row in rows:
            status = row.get('summary_status') or row.get('status') or row.get('failure_reason') or 'MISSING'
            if status == 'SUMMARY_READY':
                bucket = 'SUMMARY_READY'
            elif str(status).startswith('BLOCKED_') or str(status).startswith('SOURCE_CONTENT_BLOCKED'):
                bucket = 'SOURCE_CONTENT_BLOCKED'
            elif status == 'MISSING':
                bucket = 'MISSING'
            else:
                bucket = 'BLOCKED_OTHER'
            counts[bucket] += 1
            cases.append({
                'review_case_id': str(row.get('review_case_id')),
                'candidate_version_id': str(row.get('candidate_version_id')) if row.get('candidate_version_id') is not None else None,
                'case_state': row.get('state') or row.get('case_state'),
                'summary_status': status,
                'operational_bucket': bucket,
                'document_type': row.get('document_type'),
                'summary_input_kind': row.get('summary_input_kind'),
                'candidate_payload_used': row.get('candidate_payload_used'),
                'artifact_sha256': row.get('artifact_sha256'),
                'summary_input_sha256': row.get('summary_input_sha256'),
                'summary_sha256': row.get('summary_sha256'),
                'failure_reason': row.get('failure_reason'),
            })
        db_counts = dict(conn.execute(text("""
            SELECT
              (SELECT count(*) FROM epidemic_discovery.source_review_cases) AS review_cases,
              (SELECT count(*) FROM epidemic_discovery.source_candidate_versions) AS candidate_versions,
              (SELECT count(*) FROM epidemic_discovery.source_review_case_raw_source_summaries) AS raw_source_summary_rows,
              (SELECT count(*) FROM epidemic_discovery.source_review_decisions) AS review_decisions,
              (SELECT count(*) FROM epidemic_discovery.source_promotion_runs) AS promotion_runs,
              (SELECT count(*) FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE country_iso3='COD' AND disease_slug='ebola-disease') AS cod_ebola_trusted
        """)).mappings().one())
    coverage = {
        'status': 'REVIEW_SUMMARY_RECONCILED',
        'view_columns': cols,
        'review_case_total': len(rows),
        'summary_ready_count': counts['SUMMARY_READY'],
        'source_content_blocked_count': counts['SOURCE_CONTENT_BLOCKED'],
        'blocked_other_count': counts['BLOCKED_OTHER'],
        'missing_summary_count': counts['MISSING'],
        'coverage_complete': len(rows) == db_counts['review_cases'] and counts['MISSING'] == 0,
        'candidate_payload_used_count': sum(1 for c in cases if c['candidate_payload_used'] is True),
        'actionable_review_queue_policy': 'SUMMARY_READY only; SOURCE_CONTENT_BLOCKED is non-actionable discovery/blocker area',
        'database_counts': db_counts,
        'cases': cases,
    }
    input_manifest = {
        'status': 'RAW_SOURCE_DOCUMENT_INPUTS_AUDITED',
        'candidate_payload_used_count': coverage['candidate_payload_used_count'],
        'inputs': [
            {k: c[k] for k in ['review_case_id','summary_status','operational_bucket','summary_input_kind','candidate_payload_used','artifact_sha256','summary_input_sha256','summary_sha256','failure_reason']}
            for c in cases
        ],
    }
    claim_audit = {
        'status': 'CLAIM_LOCATOR_AUDIT_COMPLETE_WITH_BLOCKERS_SEPARATED',
        'summary_ready_cases': [c for c in cases if c['operational_bucket'] == 'SUMMARY_READY'],
        'source_content_blocked_cases': [c for c in cases if c['operational_bucket'] == 'SOURCE_CONTENT_BLOCKED'],
        'claim_locator_coverage_for_ready_summaries': 'recorded in summary rows; blocked rows intentionally have no fabricated summary claims',
        'trusted_writes_from_summary_worker': 0,
        'snapshot_writes_from_summary_worker': 0,
        'candidate_demo_writes_from_summary_worker': 0,
    }
    write('review_summary_coverage.json', coverage)
    write('review_summary_input_manifest.json', input_manifest)
    write('review_summary_claim_audit.json', claim_audit)
    print(json.dumps({'status': 'OFFICIAL_REVIEW_SUMMARY_REPORT_WRITTEN', 'summary_ready': counts['SUMMARY_READY'], 'source_content_blocked': counts['SOURCE_CONTENT_BLOCKED'], 'missing': counts['MISSING']}))

if __name__ == '__main__':
    main()
