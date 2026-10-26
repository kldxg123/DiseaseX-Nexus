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
 for schema,table in [('epidemic','diseases'),('epidemic','metric_definitions'),('epidemic','normalized_observations'),('epidemic','data_sources'),('epidemic','raw_artifacts'),('epidemic','source_fetches'),('epidemic','retrieval_runs'),('epidemic','dataset_snapshots'),('epidemic_p1_12','trusted_source_contracts'),('epidemic_p1_12','trusted_observation_lineage')]:
  rows=[dict(r) for r in c.execute(text('select column_name,data_type,is_nullable,column_default from information_schema.columns where table_schema=:s and table_name=:t order by ordinal_position'),{'s':schema,'t':table}).mappings()]
  out[f'{schema}.{table}']=rows
 out['diseases']=[dict(r) for r in c.execute(text('select * from epidemic.diseases order by slug')).mappings()]
 out['metric_like_flu']=[dict(r) for r in c.execute(text("select metric_code,display_name,unit,active from epidemic.metric_definitions where metric_code ilike '%flu%' or metric_code ilike '%influenza%' order by metric_code")).mappings()]
 (REPORT/'schema_probe.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
 print(json.dumps({'diseases':out['diseases'],'metric_like_flu':out['metric_like_flu']},ensure_ascii=False,sort_keys=True,default=str))
