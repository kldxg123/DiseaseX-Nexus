from __future__ import annotations
import json, os
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
PROJECT=Path(os.environ['PROJECT']); REPORT=PROJECT/'reports/ebola_historical_and_real_influenza_region_week_pipeline'; REPORT.mkdir(parents=True,exist_ok=True)
def db_url():
 u=os.environ['MIGRATION_DATABASE_URL']
 if os.environ.get('POSTGRES_ADMIN_PASSWORD') and os.environ.get('POSTGRES_ADMIN_USER'):
  u=make_url(u).set(username=os.environ['POSTGRES_ADMIN_USER'],password=os.environ['POSTGRES_ADMIN_PASSWORD'])
 return u
e=create_engine(db_url(),hide_parameters=True,future=True)
@event.listens_for(e,'connect')
def sec(conn,_):
 cur=conn.cursor(); cur.execute('SET search_path TO pg_catalog'); cur.execute("SET timezone TO 'UTC'"); cur.close()
with e.connect() as c:
 out={}
 for schema,table in [('epidemic','query_requests'),('epidemic','dataset_snapshot_observations'),('epidemic_p1_12','trusted_snapshot_lineage')]:
  out[f'{schema}.{table}']=[dict(r) for r in c.execute(text('select column_name,data_type,is_nullable,column_default from information_schema.columns where table_schema=:s and table_name=:t order by ordinal_position'),{'s':schema,'t':table}).mappings()]
 (REPORT/'schema_probe_extra.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
 print(json.dumps(out,ensure_ascii=False,indent=2,default=str))
