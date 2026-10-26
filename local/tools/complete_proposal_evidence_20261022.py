"""Bounded offline verification and public bibliographic search for the proposal."""
from __future__ import annotations
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from xml.etree import ElementTree as ET
from package_local_snapshot import sanitize

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/proposal_evidence_20261022'
OUT.mkdir(parents=True,exist_ok=True)

def save(name,obj):
    value=json.dumps(obj,ensure_ascii=False,indent=2)
    (OUT/name).write_text(sanitize(value)[0],encoding='utf-8')

def intervals():
    base=ROOT/'reports/independent_rsos_comparison_20260828/test_paired_predictions.csv'
    long=ROOT/'kg_paper_benchmark_work/out/rolling_origin_predictions.csv'
    with base.open(encoding='utf-8-sig',newline='') as f: paired=list(csv.DictReader(f))
    with long.open(encoding='utf-8-sig',newline='') as f: preds=list(csv.DictReader(f))
    key=lambda r:(int(r['origin_index']),int(r['horizon']),int(r['target_index']))
    lookup={(key(r),r['model']):r for r in preds}
    results={}
    for model in ['paper_arima','project_kg_ensemble','project_qwen_tuned','last_value']:
        errors=[]; coverage=[]; widths=[]; scores=[]; matched=[]; misses=[]; invalid=[]
        for p in paired:
            r=lookup.get((key(p),model))
            if r is None: misses.append(key(p)); continue
            if abs(float(r['yhat'])-float(p[model]))>1e-6 or abs(float(r['actual'])-float(p['actual']))>1e-9:
                misses.append(key(p)); continue
            y=float(r['actual']); lo=float(r['lower']); hi=float(r['upper'])
            if lo>hi: invalid.append(key(p)); continue
            hit=lo<=y<=hi
            matched.append(key(p)); coverage.append(hit)
            errors.append(abs(y-float(r['yhat']))); widths.append(hi-lo)
            scores.append(hi-lo+40*max(lo-y,0)+40*max(y-hi,0))
        valid=len(matched)==len(paired) and not misses and not invalid
        results[model]={'all_keys_and_points_match':valid,'matched':len(matched),'unmatched':len(misses),'invalid_intervals':len(invalid),
                        'coverage_95':sum(coverage)/len(coverage) if coverage else None,
                        'mean_width_95':sum(widths)/len(widths) if widths else None,
                        'mean_interval_score_95':sum(scores)/len(scores) if scores else None,
                        'wape':sum(errors)/sum(abs(float(p['actual'])) for p in paired) if valid else None}
    # Is this particular project composite still the independent 0.75/0.25 output?
    weighted=max(abs(float(p['project_kg_ensemble'])-(.75*float(p['project_qwen_tuned'])+.25*float(p['last_value']))) for p in paired)
    # No declaration that this historical interval equals a newly constructed ensemble interval.
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'paired_rows':len(paired),'long_rows':len(preds),
            'paired_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),'interval_source_sha256':hashlib.sha256(long.read_bytes()).hexdigest(),
            'composite_point_max_absolute_difference':weighted,
            'interval_score_formula':'(upper-lower) + 2/0.05*max(lower-y,0) + 2/0.05*max(y-upper,0)',
            'interpretation':'Historical saved 95% interval audit only; not a new blind trial and not multi-level WIS.',
            'models':results}
    save('interval_recomputation.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

def searches():
    queries=[
        '("infectious disease"[Title/Abstract] OR epidemic[Title/Abstract]) AND forecast*[Title/Abstract] AND ("large language model"[Title/Abstract] OR "knowledge graph"[Title/Abstract])',
        '(epidemic[Title/Abstract] OR "public health"[Title/Abstract]) AND (agent*[Title/Abstract]) AND ("large language model"[Title/Abstract] OR "knowledge graph"[Title/Abstract])',
    ]
    records=[]
    for i,q in enumerate(queries,1):
        url='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?'+urllib.parse.urlencode({'db':'pubmed','term':q,'retmode':'json','retmax':30,'sort':'relevance'})
        entry={'id':f'S{i}','database':'PubMed','query':q,'request_url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),'retmax':30}
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'AcademicProposalEvidenceAudit/1.0'})
            with urllib.request.urlopen(req,timeout=25) as r: raw=r.read(2000000)
            response=json.loads(raw); save(f'pubmed_S{i}_raw.json',response)
            result=response['esearchresult']; entry.update({'status':'success','total_hits':int(result['count']),'returned_pmids':result.get('idlist',[]),'query_translation':result.get('querytranslation'),'response_sha256':hashlib.sha256(raw).hexdigest()})
        except Exception as exc:
            entry.update({'status':'failed','error_type':type(exc).__name__})
        records.append(entry)
        time.sleep(.5)
    save('literature_search_log.json',records)
    print(json.dumps(records,ensure_ascii=False,indent=2))

def tests():
    candidates=['tests/test_generic_csv_parser.py','tests/test_generic_json_api_parser.py',
                'tests/test_candidate_normalizer.py','tests/test_candidate_deduper.py',
                'tests/test_predictor_metrics.py','tests/test_model_governance.py',
                'test_forecast_model_kg.py']
    testroot=OUT/'offline_tests'; testroot.mkdir(exist_ok=True)
    originals=[]
    for rel in candidates:
        p=ROOT/rel; dst=testroot/p.name
        # Copy tests without editing; isolate from unrelated API conftest configuration.
        shutil.copyfile(p,dst)
        originals.append({'path':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    env=dict(os.environ)
    env['PYTHONPATH']=os.pathsep.join([str(ROOT/'kg_deploy_staging/src'),str(ROOT/'packages'),str(ROOT)])
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    env['PYTHONDONTWRITEBYTECODE']='1'
    if (OUT/'unit_test_run.json').exists():
        shutil.copyfile(OUT/'unit_test_run.json',OUT/'unit_test_run_initial.json')
    args=['--noconftest','-o','addopts=','--rootdir',str(testroot),str(testroot),'-q','--junitxml',str(OUT/'unit_tests.xml')]
    runner='import socket,sys,pytest; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError("NETWORK_DISABLED_FOR_AUDIT")); sys.exit(pytest.main('+repr(args)+'))'
    command=[str(ROOT/'.venv/Scripts/python.exe'),'-c',runner]
    run=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    output=sanitize(run.stdout+'\n'+run.stderr)[0]
    (OUT/'unit_tests_output.txt').write_text(output,encoding='utf-8')
    save('unit_test_run.json',{'returncode':run.returncode,'test_files':originals,'configuration':'isolated test copies; --noconftest; plugin autoload disabled; only fixture-based unit tests','output':output})
    print(output)

def screening():
    records=json.loads((OUT/'literature_search_log.json').read_text(encoding='utf-8'))
    ids=sorted({p for x in records for p in x.get('returned_pmids',[])})
    url='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?'+urllib.parse.urlencode({'db':'pubmed','id':','.join(ids),'retmode':'xml'})
    req=urllib.request.Request(url,headers={'User-Agent':'AcademicProposalEvidenceAudit/1.0'})
    with urllib.request.urlopen(req,timeout=25) as r: raw=r.read(3000000)
    root=ET.fromstring(raw)
    (OUT/'pubmed_screened_metadata.xml').write_bytes(raw)
    entries=[]
    for article in root.findall('.//PubmedArticle'):
        pmid=article.findtext('./MedlineCitation/PMID')
        title=''.join(article.find('./MedlineCitation/Article/ArticleTitle').itertext())
        abstract=' '.join(''.join(x.itertext()) for x in article.findall('.//AbstractText'))
        doi=next((x.text for x in article.findall('.//ArticleId') if x.get('IdType')=='doi'),None)
        entries.append({'pmid':pmid,'doi':doi,'title':title,'abstract':abstract,'decision':'pending manual screening'})
    save('literature_screening.json',{'retrieval_url':url,'raw_sha256':hashlib.sha256(raw).hexdigest(),'unique_retrieved':len(ids),'returned_articles':len(entries),'articles':entries})
    print(json.dumps([{'pmid':x['pmid'],'doi':x['doi'],'title':x['title']} for x in entries],ensure_ascii=False,indent=2))

if __name__=='__main__':
    {'intervals':intervals,'searches':searches,'tests':tests,'screening':screening}[sys.argv[1]]()
