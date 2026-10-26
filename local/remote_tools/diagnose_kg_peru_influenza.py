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
    with psycopg.connect(url, options="-c default_transaction_read_only=on -c statement_timeout=10000") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT disease_slug, country_alpha3, metric_code, count(*) AS n,
                       min(period_start)::text, max(period_end)::text,
                       count(DISTINCT location_key) AS locs
                FROM epidemic.dashboard_history_series_v2
                WHERE lower(country_alpha3) IN ('per','peru')
                   OR lower(disease_slug) LIKE '%flu%'
                   OR lower(disease_slug) LIKE '%influenza%'
                GROUP BY disease_slug,country_alpha3,metric_code
                ORDER BY disease_slug,country_alpha3,metric_code
                LIMIT 120
                """
            )
            rows = cur.fetchall()
            print("dashboard_history_matches=" + str(len(rows)))
            for row in rows:
                print("HISTORY\t" + "\t".join(str(x) for x in row))

            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='epidemic'
                  AND (table_name ILIKE '%forecast%' OR table_name ILIKE '%prediction%')
                ORDER BY table_name
                LIMIT 80
                """
            )
            print("forecast_tables=" + ",".join(r[0] for r in cur.fetchall()))

            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='epidemic'
                  AND (table_name ILIKE '%dashboard%' OR table_name ILIKE '%history%' OR table_name ILIKE '%series%')
                ORDER BY table_name
                LIMIT 120
                """
            )
            print("series_tables=" + ",".join(r[0] for r in cur.fetchall()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
