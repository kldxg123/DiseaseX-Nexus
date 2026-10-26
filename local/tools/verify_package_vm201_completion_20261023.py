"""Independent numeric reconciliation and credential-free evidence packaging."""
from pathlib import Path
from collections import defaultdict,Counter
from zipfile import ZipFile,ZIP_DEFLATED
import csv,json,hashlib,re,difflib,sys
import numpy as np
from lxml import etree as E
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import frozen_llm_validation_core_20261023 as c
DATA=ROOT/'reports/evidence_completion_20261023';OUT=ROOT/'output/proposal_completion_20261023'
def load(name):return json.loads((DATA/name).read_text(encoding='utf-8'))
def sha(value):return hashlib.sha256(value).hexdigest()

def main():
    with (DATA/'factorial_predictions.csv').open(encoding='utf-8') as stream:raw=list(csv.DictReader(stream))
    rows=[];groups=defaultdict(list)
    for item in raw:
        r=dict(item)
        for key in ('origin','horizon','validation_origins'):r[key]=int(r[key])
        for key in ('actual','point','wis','coverage95','width95',*[f'q{k}' for k in ('025','10','25','50','75','90','975')]):r[key]=float(r[key])
        assert r['target_week'].startswith('2024-') and r['review_state']=='pending analyst review'
        q=np.array([r['q'+k] for k in ('025','10','25','50','75','90','975')]);sc=c.score(r['actual'],q)
        for k in sc:assert abs(sc[k]-r[k])<1e-8
        rows.append(r);groups[r['disease'],r['arm'],r['validation_origins']].append(r)
    assert len(rows)==7272 and len(groups)==36
    max_error=0.
    for expected in load('factorial_metrics.json'):
        key=expected['disease'],expected['arm'],expected['validation_origins'];rs=groups[key]
        assert len(rs)==202 and len({(r['origin'],r['horizon']) for r in rs})==202
        actual=c.aggregate(rs)
        for k in actual:
            error=abs(actual[k]-expected[k]);max_error=max(max_error,error);assert error<1e-8
    for expected in load('factorial_confidence_intervals.json'):
        a,b=expected['comparison'].split(' minus ');d=expected['disease'];result=c.paired_ci(groups[d,a,13],groups[d,b,13],expected['block_origins'])
        assert abs(result['delta_wape_pp']-expected['delta_wape_pp'])<1e-9 and np.allclose(result['ci95_pp'],expected['ci95_pp'],rtol=0,atol=1e-9)
    proposals=load('factorial_proposals.json');traces=load('factorial_selection_traces.json')
    assert len(traces)==1872
    for r in traces:
        prop=next(v for v in proposals if v['disease']==r['disease'] and v['arm']==r['arm'])
        assert tuple(r['candidate_pool'])==c.pool(prop['ids']) and set(c.BASE)<=set(r['candidate_pool'])
        assert r['selected']==min(r['loss'],key=lambda m:(r['loss'][m],c.ALL.index(m)))
    availability=load('availability_traces_v2.json');assert len(availability)==6615
    for expected in load('availability_summary_v2.json'):
        rs=[r for r in availability if (r['disease'],r['available_pct'],r['threshold_pct'])==(expected['disease'],expected['available_pct'],expected['threshold_pct'])];done=[r for r in rs if r['accepted']]
        assert len(rs)==245 and len(done)==expected['accepted']
        denominator=sum(r['actual_sum'] for r in done)
        if denominator:assert abs(sum(r['absolute_error_sum'] for r in done)/denominator-expected['conditional_wape'])<1e-12
        else:assert expected['conditional_wape'] is None
        assert all(r['imputed_observations']==0 for r in rs)
    wall=load('wall_budget_results.json');assert len(wall)==18
    for r in wall:
        if r['completed_within_budget']:assert not r['timeout'] and r['return_code']==0 and r['result']['completed_targets']==202
        elif r['timeout']:assert r['result'] is None
        else:raise AssertionError('Unexpected failed budget task')
    document=OUT/'安浩然_开题报告_vm201实验补充完整修订稿_20261023.docx'
    with ZipFile(document) as z:
        tree=E.fromstring(z.read('word/document.xml'))
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    paragraphs=[''.join(p.itertext()) for p in tree.xpath('//w:p',namespaces=ns)]
    # Native text nodes avoid counting duplicated field representations.
    paragraphs=[''.join(p.xpath('.//w:t/text()',namespaces=ns)) for p in tree.xpath('//w:p',namespaces=ns)]
    refs=[p for p in paragraphs if re.match(r'^\[\d+\]',p)];assert len(refs)==38
    seen=[]
    for p in paragraphs:
        if re.match(r'^\[\d+\]',p):continue
        for block in re.findall(r'\[([\d,，、\-–]+)\]',p):
            for part in re.split('[,，、]',block):
                bounds=re.split('[-–]',part)
                values=range(int(bounds[0]),int(bounds[1])+1) if len(bounds)==2 else [int(part)]
                for mark in values:
                    if mark not in seen:seen.append(mark)
    assert seen==list(range(1,39))
    report={'numeric_rows':len(rows),'factorial_runs':len(groups),'targets_per_disease_arm_budget':202,'maximum_metric_difference':max_error,
        'paired_confidence_intervals_recomputed':len(load('factorial_confidence_intervals.json')),'baselines_retained_all_traces':True,'contracts':len(traces),
        'availability_cases':len(availability),'cost_repair_prediction_invariance':load('availability_cost_repair_verified.json')['all_original_acceptance_and_predictions_preserved'],
        'hard_budget_tasks':len(wall),'hard_budget_timeouts':sum(r['timeout'] for r in wall),'complete_true_llm_proposals_factorial':6,'complete_true_llm_proposals_wall_budget':3,
        'authorization_tests':load('test_result.json'),'ledger_audit':{k:v for k,v in load('ledger_audit.json').items() if k!='files'},
        'document_pages':len(PdfReader(OUT/'qa/review.pdf').pages),'references':len(refs),'citation_order_verified':True,'equations':len(tree.xpath('//m:oMath',namespaces=ns)),
        'document_sha256':sha(document.read_bytes()),'production_validation':False,'human_review':'pending','prospective_12_weeks':'not complete','Docker':'prohibited on vm201'}
    (OUT/'独立复算核查.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    module=ROOT/'packages/predictor_agent/durable_execution.py'
    patch=''.join(difflib.unified_diff([],module.read_text(encoding='utf-8').splitlines(True),fromfile='/dev/null',tofile='packages/predictor_agent/durable_execution.py'))
    (OUT/'新增执行边界代码差分.patch').write_text(patch,encoding='utf-8')
    mapping={}
    for path in DATA.iterdir():
        if path.is_file() and path.suffix in ('.json','.csv','.txt'):mapping['results/'+path.name]=path
    code=['run_vm201_completion_20261023.py','test_vm201_completion_20261023.py','run_vm201_wall_budget_20261023.py','repair_vm201_availability_cost_20261023.py',
        'run_existing_qwen3_validation_20261023.py','run_frozen_llm_validation_20261023.py','frozen_llm_validation_core_20261023.py']
    for name in code:mapping['tools/'+name]=ROOT/'tools'/name
    for name in ['durable_execution.py','chronological_validation.py']:mapping['packages/predictor_agent/'+name]=ROOT/'packages/predictor_agent'/name
    mapping['inputs/graph.json']=ROOT/'kg_deploy_staging/reference/forecast_model_knowledge_graph.v1.json'
    mapping['inputs/official_response.json']=ROOT/'reports/frozen_llm_validation_20261023/official_response.json'
    mapping['inputs/new_window_observations.csv']=ROOT/'reports/frozen_llm_validation_20261023/new_window_observations.csv'
    mapping['inputs/model_fingerprints.json']=ROOT/'reports/frozen_llm_validation_20261023/existing_model_fingerprints.json'
    for name in ['独立复算核查.json','文档修改核查.json','新增执行边界代码差分.patch','授权状态迁移.drawio','授权状态迁移.svg','实验补充与复跑说明.txt']:
        mapping['review/'+name]=OUT/name
    archive=OUT/'vm201新增自动实验_代码数据与复核结果_20261023.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        manifest={}
        for name,path in mapping.items():
            payload=path.read_bytes()
            if path.suffix in ('.json','.csv','.py','.txt','.patch','.drawio','.svg'):
                content=payload.decode('utf-8-sig')
                assert not re.search(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])',content),'Address-like value in evidence export'
                assert not re.search(r'-----BEGIN .*PRIVATE KEY|USER_HOME/\\]|id_ed25519',content)
            z.writestr(name,payload);manifest[name]=sha(payload)
        z.writestr('manifest_sha256.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with ZipFile(archive) as z:
        assert z.testzip() is None
        manifest=json.loads(z.read('manifest_sha256.json'));assert all(sha(z.read(name))==value for name,value in manifest.items())
    print(json.dumps({'numeric_reconciliation':True,'document_pages':report['document_pages'],'package_files':len(mapping)+1,'package_bytes':archive.stat().st_size}))

if __name__=='__main__':main()
