import psycopg

DSN = "postgresql://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"

with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    for table in ["epidemic_kg.kg_forecast_shadow_runs", "epidemic_kg.kg_forecast_shadow_values"]:
        schema, name = table.split(".")
        cur.execute(
            "select column_name,data_type,is_nullable from information_schema.columns where table_schema=%s and table_name=%s order by ordinal_position",
            (schema, name),
        )
        print(table, cur.fetchall())
    cur.execute(
        """
        select conname, pg_get_constraintdef(c.oid)
        from pg_constraint c
        join pg_class t on c.conrelid=t.oid
        join pg_namespace n on t.relnamespace=n.oid
        where n.nspname='epidemic_kg'
          and t.relname in ('kg_forecast_shadow_runs','kg_forecast_shadow_values')
        order by t.relname, conname
        """
    )
    for row in cur.fetchall():
        print(row)
