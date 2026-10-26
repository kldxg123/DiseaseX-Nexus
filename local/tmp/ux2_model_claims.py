import os,json
from sqlalchemy import create_engine,text
engine=create_engine(os.environ['DASHBOARD_DATABASE_URL'],hide_parameters=True,connect_args={'options':'-c default_transaction_read_only=on -c statement_timeout=10000'})
with engine.connect() as conn:
 rows=[dict(r) for r in conn.execute(text("""
 select c.canonical_key, c.claim_category, c.object_text, c.subject_text, c.predicate_text, w.canonical_key paper_id, w.display_name paper_title, e.physical_page, e.printed_page_label, e.section_label
 from epidemic_kg.kg_claims c
 join epidemic_kg.kg_nodes w on w.node_id=c.work_node_id
 join epidemic_kg.kg_claim_evidence ce on ce.claim_id=c.claim_id
 join epidemic_kg.kg_evidence_spans e on e.evidence_span_id=ce.evidence_span_id
 where c.evidence_eligible and not c.quarantine and (lower(c.object_text) like '%glm%' or lower(c.object_text) like '%gam%' or lower(c.object_text) like '%baseline%' or lower(c.object_text) like '%xgboost%' or lower(c.object_text) like '%seasonal%' or lower(c.object_text) like '%naive%' or lower(c.object_text) like '%random forest%')
 order by c.canonical_key
 """)).mappings().all()]
print(json.dumps(rows,ensure_ascii=False,default=str)[:12000])
