"""Package traceable findings, not credentials or source datasets."""
from pathlib import Path
import hashlib
import json
from zipfile import ZipFile, ZIP_DEFLATED
from package_local_snapshot import sanitize

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/proposal_evidence_20261022'
checks=[
    {'reference':4,'url':'https://www.nature.com/articles/s41467-024-52504-1','scope':'正文、方法与数据/代码可用性；香港流感动态集成及历史/疫情后评价'},
    {'reference':5,'url':'https://www.nature.com/articles/s41467-024-50601-9','scope':'摘要、评价方法、数据修订和预测归档；不把提交资格等同技术授权'},
    {'reference':9,'url':'https://www.nature.com/articles/s43588-025-00798-6','scope':'正式摘要；作者代码https://github.com/miemieyanga/PandemicLLM；不推断通用执行治理'},
    {'reference':12,'url':'https://www.nature.com/articles/s41597-023-01960-3','scope':'摘要、图谱构建及来源说明；作者项目https://zitniklab.hms.harvard.edu/projects/PrimeKG/'},
    {'reference':13,'url':'https://www.nature.com/articles/s41746-025-01900-y','scope':'治理框架、风险分类、实施审批和持续监测；机构审批不同于任务身份绑定'},
    {'reference':20,'url':'https://www.jmir.org/2026/1/e86936/','scope':'观点全文；概念闭环、人工监督和影子测试建议，不作已部署有效性证据'},
    {'reference':27,'url':'https://pubmed.ncbi.nlm.nih.gov/42304469/','doi':'10.1186/s12942-026-00479-1','scope':'PubMed题名、摘要和作者/期刊元数据；未成功获取出版商全文，不作未报告机制不存在的判断'},
    {'reference':28,'url':'https://pubmed.ncbi.nlm.nih.gov/34802278/','scope':'题名、摘要及正式期刊元数据'},
    {'reference':29,'url':'https://pubmed.ncbi.nlm.nih.gov/36186430/','scope':'题名、摘要及正式期刊元数据'},
]
for c in checks: c['checked_date']='2026-10-22'
(OUT/'literature_source_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
sources=[
    {'source':'Hong Kong Flu Express','dataset':'https://data.gov.hk/en-data/dataset/hk-dh-chpsebcddr-flu-express/resource/dc0ce71c-4982-4936-a107-58d8e11c2e44','terms':'https://data.gov.hk/en/terms-and-conditions','status':'official catalogue and terms verified','constraints':'attribution; terms v1.2 dated 2025-05-26; resource exceptions and no endorsement'},
    {'source':'Singapore MOH','dataset':'https://data.gov.sg/datasets/d_ca168b2cb763640d72c4600a68f9909e/view','terms':'https://data.gov.sg/open-data-licence','status':'dataset licence link and terms verified','constraints':'Open Data Licence v1.0; attribution and licence link; no endorsement; no personal or unauthorised third-party rights'},
    {'source':'WHO','terms':'https://www.who.int/about/policies/publishing/data-policy/terms-and-conditions','status':'general dataset terms verified, resource-specific scope conditional','constraints':'public-health use; acknowledge WHO and data-providing countries; third-party content excluded; substantive modifications subject to the stated authorization requirement; do not extend dataset terms automatically to DON prose'},
    {'source':'ECDC','terms':'https://www.ecdc.europa.eu/en/ecdc-intellectual-property-notices','status':'intellectual property and third-party database notices verified','constraints':'CC BY 4.0 for owned material unless otherwise stated; source acknowledgement and modification statement; third-party exceptions'},
    {'source':'Brazil SINAN dengue','dataset':'https://dadosabertos.saude.gov.br/dataset/arboviroses-dengue','historical_catalogue':'https://opendatasus.saude.gov.br/dataset/groups/arboviroses-dengue','status':'resource-level licence unresolved after portal migration','constraints':'historical dataset catalogue shows attribution; new portal footer shows website-level no-derivatives licence; do not substitute one for the other; no new automated acquisition or redistribution authorized by this audit'},
]
for c in sources: c['checked_date']='2026-10-22'
(OUT/'source_terms_check.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf-8')
readme='''开题报告补充证据包\n核查日期：2026-10-22\n\n本包用于复查报告中已完成检查的依据，不表示研究任务全部完成，也不证明既有代码的个人作者身份。\n\n1. literature_search_log.json与pubmed_S1/S2_raw.json：真实检索式、命中数及返回编号。\n2. literature_screening.json及pubmed_screened_metadata.xml：23条记录，3条核心系统、3条背景保留、17条排除；逐条保留核查深度和理由。XML是PubMed返回的引文/摘要元数据，不是论文全文。\n3. literature_source_checks.json：表格文献依据及核查范围。\n4. code_inventory.json：选定范围645个Python文件指纹与接口；462个tests文件、981个静态测试函数定义不等于测试通过数。\n5. unit_tests.xml、unit_test_run.json及offline_tests：18项选定离线测试通过，业务代码未修改；首次2项夹具定位失败记录另保留。11条UTC弃用警告未隐藏。\n6. data_audit.json、graph_snapshot_audit.json：冻结数据与图谱版本的结构清点。\n7. interval_recomputation.json：194个目标键与底层历史上下界匹配，复核WAPE、95%覆盖率、宽度和区间评分，不是多层WIS或新盲测。\n8. source_terms_check.json：官方许可核查，巴西资源级条款仍未确认。\n9. audit_scripts：复核脚本。运行前应确保位于原项目tools目录并具备相应本地环境；不连接服务器，不调用付费模型。\n\n未包含服务器凭据、连接信息、私钥、环境文件、生产数据库或原始采集数据。资料包不代替导师对贡献归属的确认、人工断言审核或未实施实验。\n'''
(OUT/'说明.txt').write_text(readme,encoding='utf-8')
scripts=OUT/'audit_scripts'; scripts.mkdir(exist_ok=True)
for name in ['proposal_evidence_audit_20261022.py','complete_proposal_evidence_20261022.py']:
    raw=(ROOT/'tools'/name).read_text(encoding='utf-8'); clean=sanitize(raw)[0]
    (scripts/name).write_text(clean,encoding='utf-8')
# Include the sanitizer dependency used by audit scripts; it contains patterns, no literal credentials.
raw=(ROOT/'tools/package_local_snapshot.py').read_text(encoding='utf-8')
(scripts/'package_local_snapshot.py').write_text(sanitize(raw)[0],encoding='utf-8')
manifest=[]
for p in sorted(OUT.rglob('*')):
    if not p.is_file() or '__pycache__' in p.parts or '.pytest_cache' in p.parts or p.name=='MANIFEST.json': continue
    manifest.append({'path':p.relative_to(OUT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
(OUT/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
dest=ROOT/'output/proposal_revision_20261022/开题报告_检索与代码数据实核证据包_20261022.zip'
with ZipFile(dest,'w',ZIP_DEFLATED) as z:
    for m in manifest: z.write(OUT/m['path'],m['path'])
    z.write(OUT/'MANIFEST.json','MANIFEST.json')
print(json.dumps({'archive':str(dest),'files':len(manifest)+1,'bytes':dest.stat().st_size},ensure_ascii=False))
