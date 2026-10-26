import json

from epidemic_core.kg_r3c.dbutil import connect


QUERY = """
with series as (
  select
    country_alpha2,
    country_iso3,
    disease_id::text as disease_id,
    disease_slug,
    metric_code,
    granularity,
    count(*) as row_count,
    count(distinct period_start) as distinct_periods,
    count(*) filter (where value_numeric is null) as null_rows,
    min(period_start) as min_period,
    max(period_start) as max_period,
    count(distinct source_contract_id) as source_contracts
  from epidemic_p1_12.trusted_dashboard_series_v1
  group by country_alpha2, country_iso3, disease_id, disease_slug, metric_code, granularity
), registry as (
  select track_key, status, count(*) as registry_count
  from epidemic_kg.kg_qwen_heldout_registry
  group by track_key, status
)
select
  s.*,
  coalesce(jsonb_agg(jsonb_build_object('status', r.status, 'count', r.registry_count))
    filter (where r.track_key is not null), '[]'::jsonb) as registry
from series s
left join registry r
  on r.track_key = concat(s.country_iso3, ':', s.disease_slug, ':', s.metric_code, ':', s.granularity)
group by s.country_alpha2, s.country_iso3, s.disease_id, s.disease_slug,
         s.metric_code, s.granularity, s.row_count, s.distinct_periods,
         s.null_rows, s.min_period, s.max_period, s.source_contracts
order by s.distinct_periods desc, s.country_iso3, s.disease_slug, s.metric_code
"""


with connect() as conn:
    with conn.cursor() as cur:
        cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        cur.execute(QUERY)
        names = [desc.name for desc in cur.description]
        rows = []
        for raw in cur.fetchall():
            row = dict(zip(names, raw, strict=True))
            for key in ("min_period", "max_period"):
                row[key] = row[key].isoformat() if row[key] else None
            rows.append(row)
        cur.execute("show transaction_read_only")
        read_only = cur.fetchone()[0]
        conn.rollback()

print(
    json.dumps(
        {
            "transaction_read_only": read_only,
            "series_count": len(rows),
            "labels_read": False,
            "series_metadata": rows,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
)
