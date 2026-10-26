"""Independent result reconciliation and credential-free reproducibility package."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from collections import defaultdict,Counter
import csv,json,hashlib,re,subprocess,statistics
import numpy as np
from lxml import etree as E
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'reports/frozen_llm_validation_20261023';OUT=ROOT/'output/proposal_real_llm_holdout_20261023'
DOC=OUT/'安浩然_开题报告_三病种新增时段与真实LLM实验修订稿_20261023.docx'

def readcsv(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    checks={};quantiles=np.array([.025,.1,.25,.5,.75,.9,.975]);base={'last_value','mean4','seasonal52'}
    for prefix,n in [('historical_llm',610),('new_window',202)]:
        rows=readcsv(DATA/(prefix+'_predictions.csv'));groups=defaultdict(list)
        for r in rows:groups[r['disease'],r['arm']].append(r)
        max_wis_error=0.;max_metric_error=0.
        for (disease,arm),rs in groups.items():
            assert len(rs)==n and len({(r['origin'],r['horizon']) for r in rs})==n
            y=np.array([float(r['actual']) for r in rs]);p=np.array([float(r['point']) for r in rs]);q=np.array([[float(r['q'+k]) for k in ['025','10','25','50','75','90','975']] for r in rs])
            assert np.isfinite(q).all() and (np.diff(q,axis=1)>=0).all()
            err=y[:,None]-q
            wis=(2*np.maximum(quantiles*err,(quantiles-1)*err)).mean(axis=1)
            max_wis_error=max(max_wis_error,float(np.abs(wis-np.array([float(r['wis']) for r in rs])).max()))
            expected=next(r for r in json.loads((DATA/(prefix+'_metrics.json')).read_text()) if (r['disease'],r['arm'],r['horizon'])==(disease,arm,'all'))
            max_metric_error=max(max_metric_error,abs(float(np.abs(y-p).sum()/np.abs(y).sum())-expected['wape']))
        assert max_wis_error<1e-10 and max_metric_error<1e-12
        lookup={(r['disease'],r['arm'],r['origin'],r['horizon']):r for r in rows}
        for r in rows:
            if r['arm'] in ['real_llm','deterministic']:
                fixed=lookup[r['disease'],r['selected'],r['origin'],r['horizon']]
                assert r['point']==fixed['point'] and r['actual']==fixed['actual']
        traces=json.loads((DATA/(prefix+'_selection_traces.json')).read_text())
        for t in traces:
            assert base<=set(t['pool']) and len(t['pool'])==5 and t['selected'] in t['pool']
            assert abs(t['loss'][t['selected']]-min(t['loss'].values()))<1e-10
        checks[prefix]={'prediction_rows':len(rows),'groups':len(groups),'targets_per_arm_per_disease':n,'maximum_wis_reconciliation_error':max_wis_error,'maximum_wape_reconciliation_error':max_metric_error,'all_baselines_retained':True,'selected_predictions_match_fixed_candidates':True}
    props=[json.loads(p.read_text()) for p in DATA.glob('proposal_*.json')]
    assert len(props)==12 and sum(p['schema_valid'] for p in props)==4
    checks['real_llm']={'calls':12,'parseable_json':sum(isinstance(json.loads(p['raw_response']),dict) for p in props),'valid_optional_contracts':4,'fallback_tasks':8,'all_future_target_labels_withheld':all(p['target_labels_provided'] is False for p in props)}
    log=(DATA/'unit_tests_existing_runtime.log').read_text();assert 'Ran 12 tests' in log and log.rstrip().endswith('OK')
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main','m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}
    with ZipFile(DOC) as z:assert z.testzip() is None;xml=E.fromstring(z.read('word/document.xml'))
    paras=[''.join(p.xpath('.//w:t/text()',namespaces=ns)) for p in xml.xpath('//w:p',namespaces=ns)]
    refs=[s for s in paras if re.match(r'^\[\d+\]',s)];assert len(refs)==38
    seen=[]
    for s in paras:
        if re.match(r'^\[\d+\]',s):break
        for block in re.findall(r'\[([\d,，、\-–]+)\]',s):
            for token in re.split('[,，、]',block):
                if token.isdigit():numbers=[int(token)]
                elif re.fullmatch(r'\d+[-–]\d+',token):
                    lo,hi=map(int,re.split('[-–]',token));numbers=list(range(lo,hi+1))
                else:continue
                for v in numbers:
                    if v not in seen:seen.append(v)
    assert seen==list(range(1,39)),seen
    assert len(xml.xpath('//m:oMath',namespaces=ns))==2
    assert not any('新窗口未冻结' in s for s in paras)
    abstract=next(s for s in paras if s.startswith('本课题研究面向多病种疫情监测的可信智能预测系统。'));assert len(abstract)<=400
    pdf=PdfReader(str(OUT/'qa/review.pdf'))
    checks['document']={'references':38,'first_citation_order_verified':True,'native_equations':2,'abstract_characters':len(abstract),'pages':len(pdf.pages),'sha256':sha(DOC)}
    checks['source_versions']={'weekly_annual_table_dengue_2024_sum':13606,'year_end_bulletin_dengue_2024_cumulative':13610,'difference_not_imputed':4,'same_source_campylobacter_2024':462,'same_source_mumps_2024':235,'not_final_confirmed_annual_surveillance_total':True}
    (OUT/'独立复算与文档核查.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    # Keep only intentional evidence, not private access helpers, weights, or original restricted workbooks.
    keep=[]
    for p in DATA.iterdir():
        if p.is_file() and (p.name.startswith(('historical_llm_','new_window_','proposal_','protocol')) or p.name in ['official_response.json','result_summary.json','existing_model_fingerprints.json','existing_model_load.json','unit_tests_existing_runtime.log','frozen_llm_validation_core_20261023.py','run_frozen_llm_validation_20261023.py','run_existing_qwen3_validation_20261023.py','test_frozen_llm_validation_20261023.py']):
            if p.suffix in ['.json','.csv','.py','.log']:keep.append((p,'experiment/'+p.name))
    for name in ['fetch_validation_annual_sources_20261023.py','prepare_validation_new_window_20261023.py']:
        keep.append((ROOT/'tools'/name,'code/'+name))
    keep += [(ROOT/'reports/three_disease_chronological_20261023/provenance.json','sources/original_2012_2022_provenance.json'),
      (DATA/'new_window_source/provenance.json','sources/new_annual_tables_provenance.json'),
      (OUT/'独立复算与文档核查.json','verification.json'),(OUT/'图2数据.csv','figure_data.csv'),(OUT/'新增时段三病种一步预测.svg','figure.svg'),(OUT/'实验结果与批注更新说明.txt','README.txt')]
    protected_ip=re.compile(rb'(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])')
    for p,_ in keep:
        data=p.read_bytes()
        assert b'-----BEGIN OPENSSH PRIVATE KEY-----' not in data
        assert b'-----BEGIN RSA PRIVATE KEY-----' not in data
        assert not protected_ip.search(data),p.name
    manifest={arc:sha(p) for p,arc in keep};zip_path=OUT/'新增时段与真实LLM实验_代码数据与结果_20261023.zip'
    with ZipFile(zip_path,'w',ZIP_DEFLATED) as z:
        for p,arc in keep:z.write(p,arc)
        z.writestr('manifest_sha256.json',json.dumps(manifest,indent=2))
    with ZipFile(zip_path) as z:
        assert z.testzip() is None
        for arc,digest in manifest.items():assert hashlib.sha256(z.read(arc)).hexdigest()==digest
    print(json.dumps({'reconciliation_passed':True,'document_pages':len(pdf.pages),'package_files':len(keep)+1,'package_bytes':zip_path.stat().st_size},ensure_ascii=True))

if __name__=='__main__':main()
