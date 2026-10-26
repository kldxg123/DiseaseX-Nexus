from __future__ import annotations

from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")
SERVICE = ROOT / "src/epidemic_core/forecast_model_kg/service.py"


def main() -> int:
    text = SERVICE.read_text(encoding="utf-8")
    old = '''        rows = conn.execute(text("""
            SELECT disease_slug, country_alpha3 AS country, min(period_start) AS start_date, max(period_end) AS end_date,
                   count(*) AS period_count, count(DISTINCT location_key) AS region_count,
                   count(*) FILTER (WHERE value_numeric IS NOT NULL AND value_numeric <> 0) AS nonzero_period_count,
                   count(DISTINCT source_key) AS source_count,
                   array_agg(DISTINCT source_key) AS source_ids
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug=:disease_slug AND (CAST(:country AS text) IS NULL OR country_alpha3=CAST(:country AS text))
              AND (CAST(:target AS text) IS NULL OR metric_code=CAST(:target AS text))
            GROUP BY disease_slug, country_alpha3
            ORDER BY count(*) DESC, country_alpha3
            LIMIT 1
        """), {"disease_slug": disease_slug, "country": country_norm, "target": target}).mappings().all()
        if rows:
            row = dict(rows[0])
'''
    new = '''        base_sql = """
            SELECT disease_slug, country_alpha3 AS country, metric_code, min(period_start) AS start_date, max(period_end) AS end_date,
                   count(*) AS period_count, count(DISTINCT location_key) AS region_count,
                   count(*) FILTER (WHERE value_numeric IS NOT NULL AND value_numeric <> 0) AS nonzero_period_count,
                   count(DISTINCT source_key) AS source_count,
                   array_agg(DISTINCT source_key) AS source_ids
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug=:disease_slug AND (CAST(:country AS text) IS NULL OR country_alpha3=CAST(:country AS text))
              AND (CAST(:target AS text) IS NULL OR metric_code=CAST(:target AS text))
            GROUP BY disease_slug, country_alpha3, metric_code
            ORDER BY count(*) DESC, country_alpha3, metric_code
            LIMIT 1
        """
        rows = conn.execute(text(base_sql), {"disease_slug": disease_slug, "country": country_norm, "target": target}).mappings().all()
        metric_fallback_used = False
        if not rows and target in {None, "cases_new"}:
            fallback_sql = """
                SELECT disease_slug, country_alpha3 AS country, metric_code, min(period_start) AS start_date, max(period_end) AS end_date,
                       count(*) AS period_count, count(DISTINCT location_key) AS region_count,
                       count(*) FILTER (WHERE value_numeric IS NOT NULL AND value_numeric <> 0) AS nonzero_period_count,
                       count(DISTINCT source_key) AS source_count,
                       array_agg(DISTINCT source_key) AS source_ids
                FROM epidemic.dashboard_history_series_v2
                WHERE disease_slug=:disease_slug AND (CAST(:country AS text) IS NULL OR country_alpha3=CAST(:country AS text))
                GROUP BY disease_slug, country_alpha3, metric_code
                ORDER BY
                  CASE
                    WHEN metric_code IN ('cases_new','influenza_ili_cases','influenza_sari_cases') THEN 0
                    WHEN metric_code LIKE '%positive%' OR metric_code LIKE '%detections%' THEN 1
                    ELSE 2
                  END,
                  count(*) DESC,
                  metric_code
                LIMIT 1
            """
            rows = conn.execute(text(fallback_sql), {"disease_slug": disease_slug, "country": country_norm}).mappings().all()
            metric_fallback_used = bool(rows)
        if rows:
            row = dict(rows[0])
            row["metric_fallback_used"] = metric_fallback_used
'''
    if old not in text:
        raise RuntimeError("query block anchor not found")
    text = text.replace(old, new)

    old_profile = '''        "forecast_target": target or "cases_new",
'''
    new_profile = '''        "forecast_target": target or "cases_new",
        "metric_fallback_used": False,
'''
    if '"metric_fallback_used": False' not in text:
        text = text.replace(old_profile, new_profile)

    old_update = '''            "nonzero_period_count": nonzero,
            "zero_ratio": None if period_count == 0 else round(1 - (nonzero / period_count), 6),
'''
    new_update = '''            "forecast_target": str(row.get("metric_code") or target or "cases_new"),
            "metric_fallback_used": bool(row.get("metric_fallback_used")),
            "nonzero_period_count": nonzero,
            "zero_ratio": None if period_count == 0 else round(1 - (nonzero / period_count), 6),
'''
    if '"metric_fallback_used": bool(row.get("metric_fallback_used"))' not in text:
        text = text.replace(old_update, new_update)
    SERVICE.write_text(text, encoding="utf-8")
    print("patch=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
