import json, os
from sqlalchemy import create_engine, text
from epidemic_core.forecast_model_kg.r3r import _alpha2, _disease_slug, _series
engine=create_engine(os.environ['DASHBOARD_DATABASE_URL'], hide_parameters=True, connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000'})
res={}
for country in ['Brazil','BRA','BR','France','FRA','FR']:
    res[country]={'alpha2':_alpha2(engine,country),'dengue_series':_series(engine,'dengue',country,'cases_new','week'),'covid_series':_series(engine,'covid_19',country,'cases_cumulative','week')}
with engine.connect() as conn:
    rows=conn.execute(text("select display_name, properties->>'country_alpha2' a2, properties->>'country_alpha3' a3, properties->>'country_name' cname, properties->>'disease_slug' disease, properties->>'metric_code' metric, properties->>'frequency' freq from epidemic_kg.kg_nodes where node_type='DATA_SERIES' and display_name ilike '%dengue%' limit 5")).mappings().all()
    res['sample']=[dict(r) for r in rows]
print(json.dumps(res, ensure_ascii=False, default=str))
