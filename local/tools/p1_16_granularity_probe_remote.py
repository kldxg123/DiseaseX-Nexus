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
 cons=[dict(r) for r in c.execute(text("select conname, pg_get_constraintdef(oid) as def from pg_constraint where conrelid='epidemic.normalized_observations'::regclass and conname like '%granularity%' order by conname")).mappings()]
 vals=[dict(r) for r in c.execute(text("select granularity,count(*) from epidemic.normalized_observations group by granularity order by granularity")).mappings()]
 print(json.dumps({'constraints':cons,'values':vals},ensure_ascii=False,indent=2,default=str))
