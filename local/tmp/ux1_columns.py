import json, os
from sqlalchemy import create_engine, text
engine=create_engine(os.environ['DASHBOARD_DATABASE_URL'], hide_parameters=True, connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000'})
with engine.connect() as conn:
 rows=conn.execute(text("select column_name from information_schema.columns where table_schema='epidemic_kg' and table_name='kg_nodes' order by ordinal_position")).scalars().all()
print(json.dumps(rows, ensure_ascii=False))
