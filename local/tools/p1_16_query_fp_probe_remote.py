from __future__ import annotations
import json, os
from sqlalchemy import create_engine,event,text
from sqlalchemy.engine import make_url
def db_url():
 u=os.environ['MIGRATION_DATABASE_URL']
 if os.environ.get('POSTGRES_ADMIN_PASSWORD') and os.environ.get('POSTGRES_ADMIN_USER'):
  u=make_url(u).set(username=os.environ['POSTGRES_ADMIN_USER'],password=os.environ['POSTGRES_ADMIN_PASSWORD'])
 return u
e=create_engine(db_url(),hide_parameters=True,future=True)
@event.listens_for(e,'connect')
def sec(conn,_): cur=conn.cursor(); cur.execute('SET search_path TO pg_catalog'); cur.close()
with e.connect() as c:
 rows=[dict(r) for r in c.execute(text("select fingerprint,raw_country,raw_disease,country_alpha2,start_date,end_date from epidemic.query_requests order by created_at desc limit 20")).mappings()]
 cons=[dict(r) for r in c.execute(text("select conname, pg_get_constraintdef(oid) as def from pg_constraint where conrelid='epidemic.query_requests'::regclass order by conname")).mappings()]
 print(json.dumps({'recent':rows,'constraints':cons},ensure_ascii=False,indent=2,default=str))
