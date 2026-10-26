import json, os
from sqlalchemy import create_engine, text
from epidemic_core.forecast_model_kg.r3r import _alpha2, _series, _disease_slug
engine=create_engine(os.environ['DASHBOARD_DATABASE_URL'], hide_parameters=True, connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=5000'})
out={}
for country,disease,target in [('Brazil','dengue','cases_new'),('France','covid_19','cases_cumulative')]:
    alpha2=_alpha2(engine,country); slug=_disease_slug(disease)
    params={'disease_slug':slug,'alpha2':alpha2,'country':country,'target':target,'frequency':'week'}
    sql="""
        select node_id::text as series_id, display_name, properties->>'country_alpha2' a2, properties->>'country_alpha3' a3, properties->>'country_name' cname, properties->>'disease_slug' disease, properties->>'disease_name' dname, properties->>'metric_code' metric, properties->>'frequency' freq, quarantine
        from epidemic_kg.kg_nodes
        where node_type='DATA_SERIES'
          and coalesce(quarantine,false)=false
          and (:disease_slug is null or properties->>'disease_slug'=:disease_slug or properties->>'disease_name' ilike ('%'||:disease_slug||'%'))
          and (:alpha2 is null or properties->>'country_alpha2'=:alpha2 or properties->>'country_alpha3'=:country or properties->>'country_name' ilike ('%'||:country||'%'))
          and (:target is null or properties->>'metric_code'=:target)
          and (:frequency is null or properties->>'frequency'=:frequency)
        order by coalesce((properties->>'observation_count')::int,0) desc, display_name
        limit 3
    """
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text(sql),params).mappings().all()]
        relaxed=[dict(r) for r in conn.execute(text("select node_id::text series_id, display_name, properties->>'metric_code' metric, properties->>'frequency' freq, properties->>'disease_slug' disease, properties->>'country_alpha2' a2 from epidemic_kg.kg_nodes where node_type='DATA_SERIES' and properties->>'country_alpha2'=:alpha2 limit 10"),{'alpha2':alpha2}).mappings().all()]
    out[country]={'alpha2':alpha2,'slug':slug,'params':params,'series_func':_series(engine,disease,country,target,'week'),'manual_rows':rows,'relaxed':relaxed}
print(json.dumps(out, ensure_ascii=False, default=str))
