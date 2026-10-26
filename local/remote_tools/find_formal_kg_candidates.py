from __future__ import annotations

import pathlib

import psycopg


ROOT = pathlib.Path("USER_HOME/projects/openclaw")


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / "runtime/secrets/vm201_runtime.env").read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key] = value.strip().strip('"').strip("'")
    return out


def main() -> int:
    env = load_env()
    role_pass = env.get("EPIDEMIC_ROLE_PASS")
    if not role_pass:
        print("db=FAILED_ROLE_PASS_MISSING")
        return 2
    url = f"postgresql://epidemic_dashboard_ro:{role_pass}@127.0.0.1:55432/epidemic_openclaw"
    with psycopg.connect(url, options="-c default_transaction_read_only=on -c statement_timeout=15000") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH s AS (
                  SELECT disease_slug, country_alpha3, metric_code,
                         min(period_start) AS start_date,
                         max(period_end) AS end_date,
                         count(*) AS period_count,
                         count(DISTINCT location_key) AS region_count,
                         count(*) FILTER (WHERE value_numeric IS NOT NULL AND value_numeric <> 0) AS nonzero_count
                  FROM epidemic.dashboard_history_series_v2
                  GROUP BY disease_slug, country_alpha3, metric_code
                )
                SELECT disease_slug, country_alpha3, metric_code,
                       period_count,
                       GREATEST(1, ((end_date::date - start_date::date + 6) / 7)::int) AS history_weeks,
                       region_count,
                       nonzero_count,
                       start_date::text,
                       end_date::text
                FROM s
                WHERE GREATEST(1, ((end_date::date - start_date::date + 6) / 7)::int) >= 104
                  AND region_count >= 5
                  AND period_count > 0
                ORDER BY history_weeks DESC, region_count DESC, period_count DESC
                LIMIT 20
                """
            )
            rows = cur.fetchall()
            print("formal_candidate_count=" + str(len(rows)))
            for row in rows:
                print("FORMAL_CANDIDATE\t" + "\t".join(str(x) for x in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
