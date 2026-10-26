import os,json
from sqlalchemy import create_engine,text
engine=create_engine(os.environ['DASHBOARD_DATABASE_URL'],hide_parameters=True,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'})
with engine.connect() as conn:
    models=[dict(r) for r in conn.execute(text("select node_id::text, canonical_key, display_name, properties from epidemic_kg.kg_nodes where node_type='MODEL_OR_METHOD' order by canonical_key")).mappings().all()]
    cats=[dict(r) for r in conn.execute(text("select claim_category, claim_status, evidence_eligible, quarantine, count(*) n from epidemic_kg.kg_claims group by claim_category,claim_status,evidence_eligible,quarantine order by claim_category,claim_status")).mappings().all()]
print(json.dumps({'models':models,'claim_categories':cats},ensure_ascii=False,default=str))
