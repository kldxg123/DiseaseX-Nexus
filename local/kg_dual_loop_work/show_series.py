from db import connect

conn = connect()
r = conn.execute("select * from epidemic.dashboard_history_series_v2 limit 3").fetchall()
cols = [d.name for d in conn.execute("select * from epidemic.dashboard_history_series_v2 limit 0").description]
print(cols)
for row in r:
    print(dict(zip(cols, row)))
