from __future__ import annotations
import json, os
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
def db_url():
 u=os.environ['MIGRATION_DATABASE_URL']
 if os.environ.get('POSTGRES_ADMIN_PASSWORD') and os.environ.get('POSTGRES_ADMIN_USER'):
  u=make_url(u).set(username=os.environ['POSTGRES_ADMIN_USER'],password=os.environ['POSTGRES_ADMIN_PASSWORD'])
 return u
e=create_engine(db_url(),hide_parameters=True,future=True)
@event.listens_for(e,'connect')
def sec(conn,_):
 cur=conn.cursor(); cur.execute('SET search_path TO pg_catalog'); cur.close()
with e.connect() as c:
 rows=[dict(r) for r in c.execute(text("select alpha2,alpha3,name from epidemic.countries where alpha2 in ('US','ZZ','XK','CD') or alpha3 in ('USA','COD') order by alpha2")).mappings()]
 print(json.dumps(rows,ensure_ascii=False,default=str))
