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
 cols=[dict(r) for r in c.execute(text("select column_name,data_type,is_nullable,column_default from information_schema.columns where table_schema='epidemic_p1_12' and table_name='trusted_fetch_artifacts' order by ordinal_position")).mappings()]
 cons=[dict(r) for r in c.execute(text("select conname, pg_get_constraintdef(oid) as def from pg_constraint where conrelid='epidemic_p1_12.trusted_fetch_artifacts'::regclass order by conname")).mappings()]
 sample=[dict(r) for r in c.execute(text("select * from epidemic_p1_12.trusted_fetch_artifacts limit 2")).mappings()]
 print(json.dumps({'columns':cols,'constraints':cons,'sample':sample},ensure_ascii=False,indent=2,default=str))
