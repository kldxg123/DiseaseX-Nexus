"""Add the six-dimension table and verified evidence to the retained proposal."""
from __future__ import annotations
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as E
from build_proposal_revision_20261022 import W, NS, w, paragraph, create_table, serialize, STYLES
from proposal_supplement_content_20261022 import *
from package_local_snapshot import sanitize

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'output/proposal_revision_20261022'
EVIDENCE=ROOT/'output/proposal_evidence_20261022'
SOURCE=WORK/'安浩然_开题报告_表一研究定位更新版_20261022.docx'
OUTPUT=WORK/'安浩然_开题报告_六维比较与实核材料补全版_20261022.docx'

def txt(node): return ''.join(node.xpath('.//w:t/text()',namespaces=NS))
def structure(node):
    return (node.tag,tuple(sorted(node.attrib.items())),node.text,node.tail,tuple(structure(x) for x in node))
def set_text(node,value):
    pr=deepcopy(node.find(f'{{{W}}}pPr'))
    for x in list(node): node.remove(x)
    if pr is not None: node.append(pr)
    r=w('r'); t=w('t'); t.text=value; r.append(t); node.append(r)
def load(name): return json.loads((EVIDENCE/name).read_text(encoding='utf-8'))
def dump(name,obj): (EVIDENCE/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def classify_search():
    result=load('literature_screening.json')
    direct={'34802278':'动态图疫情预测与交互平台','36186430':'病种知识图谱与流行病学信息平台','42304469':'自动事件提取、多源整合及预测平台'}
    background={'36996094':'个体风险本体推理，非时序模型推荐','40306628':'疫情事实核查，非数值疫情预测','42166787':'疫苗信息监测与公共沟通，非病例预测'}
    for r in result['articles']:
        pid=r['pmid']
        if pid in direct: r.update(decision='direct system relevance',reason=direct[pid],review_depth='title and abstract')
        elif pid in background: r.update(decision='background only',reason=background[pid],review_depth='title and abstract')
        else:
            if pid in {'42180341','42228755','42434700'}:
                depth='title and abstract'
                reason={'42180341':'干预依从性与移动模拟，不是预测模型推荐与执行治理','42228755':'人口合成，不是疫情监测预测任务','42434700':'公共政策情绪监测，非感染发生预测'}[pid]
            else: depth='title'; reason='题名指向药物、毒理、慢病、营养、认知、综述辅助或临床流程等非本轮目标任务'
            r.update(decision='excluded from core comparison',reason=reason,review_depth=depth)
    result['counts']={'retrieved':23,'duplicate_pmids':0,'direct':3,'background':3,'excluded':17,'abstracts_reviewed':9}
    dump('literature_screening.json',result)
    return result

def add_table(blocks,title,headers,rows,widths):
    blocks.extend([paragraph(title,'caption'),create_table(headers,rows,widths)])

def appendix():
    blocks=[paragraph(APPENDIX_HEAD,'h1'),paragraph(APPENDIX_INTRO),paragraph('A.1 文献检索与筛选','h2')]
    records=load('literature_search_log.json')
    for r in records:
        blocks.append(paragraph(f"{r['id']}完整检索式：{r['query']}。检索库为PubMed，按相关性排序，返回上限30条，实际命中{r['total_hits']}条；无语言限制。使用ESearch取得编号并用EFetch核对题名及摘要，不访问需订阅或登录的检索结果。"))
    blocks.append(paragraph('两组检索未设置起始年份，记录截至检索时已收录的条目；不以这些定向查询穷尽所有同类平台。纳入核心比较要求研究对象为感染性疾病监测、预测或知识组织平台；背景保留项用于理解证据推理、事实核查与人工评价，不能直接作为数值预测基线。排除药物相互作用、毒理、慢病、营养或其他非目标任务；对仅涉及人口模拟和政策情绪的条目经摘要复核后不纳入核心比较。'))
    add_table(blocks,'表A1 定向补充检索的筛选结果',['处置','数量','可追溯编号与理由'],[
        ['直接相关系统研究','3','PMID 34802278、36186430、42304469；纳入研究现状及差异讨论[27-29]'],
        ['背景保留','3','PMID 36996094、40306628、42166787；风险推理、事实核查及信息监测，不作预测精度对照'],
        ['排除核心比较','17','14条按题名判断为非目标任务，3条经摘要复核为人口模拟、人口合成或政策情绪研究；逐条理由见证据包'],
    ],[1500,600,5800])
    blocks.append(paragraph('表2既有代表文献通过题名、DOI、正式页面或作者公开资料定向核对，不全部来自上述23条结果。文献[4]核对方法及数据/代码可用性，文献[5]核对评价方法及预测归档，文献[9]核对正式摘要及作者代码说明，文献[12]核对图谱构建与来源资料，文献[13]核对治理流程及监测要求，文献[20]核对框架与人工监督讨论，文献[27]限于PubMed收录摘要。相应URL和核对范围保存在literature_source_checks.json中。'))
    blocks.extend([paragraph('A.2 代码快照与贡献边界','h2'),paragraph('以下路径均为项目相对路径。SHA-256仅列前12位供定位，完整值见code_inventory.json；省略号对应本地快照中的src/epidemic_core目录，不代表服务器位置。模块由谁开发不能从文件内容自动推定，故所有既有文件均先列入复用资产，本表不作为个人独立开发证明。')])
    code_display=[list(x) for x in CODE_ROWS]
    add_table(blocks,'表A2 代表性代码模块的核查记录',['模块','相对路径','SHA-256前12位','查证结果及边界'],code_display,[1100,2600,1550,2650])
    code_table=blocks[-1]
    path_lines=[['packages/','crawler_agent/','source_registry.py'],
                ['packages/','predictor_agent/','model_governance.py'],
                ['kg_deploy_staging/','.../forecast_model_kg/','engine.py'],
                ['kg_deploy_staging/','.../forecast_model_kg/','r4r3_review_input.py'],
                ['kg_paper_benchmark_work/','src/benchlib.py'],
                ['kg_paper_benchmark_work/','src/run_experiment.py']]
    for idx,row in enumerate(code_table.findall(f'{{{W}}}tr')):
        for cell in row.findall(f'{{{W}}}tc'):
            for p in cell.findall(f'{{{W}}}p'):
                pr=p.find(f'{{{W}}}pPr')
                for jc in pr.findall(f'{{{W}}}jc'): pr.remove(jc)
                pr.append(w('jc',val='center' if idx==0 else 'left'))
        if idx:
            p=row.findall(f'{{{W}}}tc')[1].find(f'{{{W}}}p')
            for run in p.findall(f'{{{W}}}r'): p.remove(run)
            for line_index,line in enumerate(path_lines[idx-1]):
                run=w('r')
                if line_index: run.append(w('br'))
                t=w('t'); t.text=line; run.append(t); p.append(run)
    blocks.append(paragraph('图谱推荐至少存在两个不同目录快照：本次执行测试所用engine.py指纹为6bc4338601ad，另一份协作快照为75213ffd037e；不能混用其测试结论。个人贡献证据应在当前冻结清单之后逐项记录新增或实质修改的接口、测试和分析，不把目录文件数、既有平台功能或公开算法等同于本人工作量。'))
    blocks.extend([paragraph('A.3 数据身份与历史实验复算','h2'),paragraph('本次不改变预测值、训练窗口、权重或模型参数，只连接历史文件并复算统计量。冻结观测表无空单元格或完全重复行，不等于证明从未缺报、修订或具有完整发布版本；vault_id保留为内部来源关联键，不替代原始官方记录。')])
    data=load('data_audit.json')
    rows=[]
    labels=['新加坡冻结观测','独立对照配对表','底层滚动预测','较大历史配对表']
    details=['412行，2012-W01至2019-W48，4列；与完整2012至2022官方目录区分',
             '194行，50个起点，1至4周；含区间命中标记，无完整分位数',
             '5,516行，含模型、点预测和上下界；不是5,516个独立观测',
             '394行，范围大于正式194目标键；仅用键连接，不混合评价身份']
    for x,label,detail in zip(data,labels,details): rows.append([label,detail,x['sha256'][:12]])
    add_table(blocks,'表A3 本地数据与预测文件的身份核查',['对象','本轮核对结果','SHA-256前12位'],rows,[1850,4500,1550])
    blocks.append(paragraph('两个模型在194个目标键上的点预测和真实值与底层文件逐项一致，区间未出现下界大于上界。项目组合点预测与0.75倍project_qwen_tuned加0.25倍last_value的最大绝对差约为5.68×10⁻¹⁴；该检查仅确认存档点预测的组合关系，不证明权重事先登记，也不据此重新构造历史区间。'))
    audit=load('interval_recomputation.json')
    vals=audit['models']; rows=[]
    for model,label in [('project_kg_ensemble','项目独立组合'),('paper_arima','重实现ARIMA')]:
        r=vals[model]; assert r['all_keys_and_points_match']
        rows.append([label,'194',f"{r['wape']*100:.2f}%",f"{r['coverage_95']*100:.2f}%",f"{r['mean_width_95']:.2f}",f"{r['mean_interval_score_95']:.2f}"])
    add_table(blocks,'表A4 同一194目标键的历史预测复核',['方法','目标键数','WAPE','95%覆盖率','95%平均宽度','95%平均区间评分'],rows,[1750,750,1100,1200,1450,1650])
    blocks.append(paragraph('区间评分按宽度加越界惩罚计算：观测低于下界时，加40倍的下界与观测差；高于上界时，加40倍的观测与上界差；落在区间内不加越界惩罚。该指标对应名义95%区间，评分和宽度越小越好，须与覆盖率共同解释。只存有这一层上下界，故不把本次区间评分称为多层WIS。现有文件已经被查看，本次重算不恢复其独立盲测身份。'))
    graph=load('graph_snapshot_audit.json')
    blocks.append(paragraph(f"V1图谱文件的SHA-256前12位为{graph['sha256'][:12]}，结构统计为54节点、86边、12规则、19证据源、7病种节点。该清点对应kg_deploy_staging/reference/forecast_model_knowledge_graph.v1.json；与论文全文库的1,574个版本及3,791条断言不是同一数据对象，未用该小图谱替代全文库质量评价。"))
    blocks.extend([paragraph('A.4 官方来源与许可核对','h2'),paragraph('条款核对记录以官方数据目录和官方许可页为依据，日期为2026年10月22日。门户层面的使用规则不自动覆盖特定文件中的第三方内容、个人资料和另列例外，亦不构成部署或数据转授权批准。')])
    add_table(blocks,'表A5 数据来源的许可核查与适用边界',['来源','已核对材料','再利用条件或限制','当前处理'],SOURCE_ROWS,[1200,1950,2750,2000])
    blocks.extend([paragraph('A.5 已完成检查与后续实证边界','h2'),paragraph(TEST_EVIDENCE),paragraph('已完成的是定向检索留档、范围明确的代码清查、数据身份核验、历史区间评分重算和选定离线测试。图谱断言的双人标注与抽样准确率、锁定后新病例窗口、至少三种病种的无泄漏对照、200项授权状态机测试、完整本地容器启动及目标用户任务研究仍属于后续研究。人的开发贡献及导师意见需要责任人确认；本地文件或自动检查不能代替签字确认。')])
    return blocks

def main():
    classify_search()
    source_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    with ZipFile(SOURCE) as z:
        infos=z.infolist(); parts={i.filename:z.read(i.filename) for i in infos}
    doc=E.fromstring(parts['word/document.xml'])
    report=doc.xpath('./w:body/w:tbl',namespaces=NS)[1]
    review=deepcopy(report.findall(f'{{{W}}}tr')[-1]); review_original=structure(review)
    cellpr=deepcopy(report.xpath('./w:tr[1]/w:tc[1]/w:tcPr',namespaces=NS)[0])
    contents=[]
    for row in report.findall(f'{{{W}}}tr')[:-1]:
        cell=row.find(f'{{{W}}}tc')
        for child in cell:
            if child.tag==f'{{{W}}}tcPr': continue
            # Discard only the tiny spacer created by the previous grouping helper.
            if child.tag==f'{{{W}}}p' and not txt(child) and child.find('w:pPr/w:spacing',NS) is not None:
                continue
            contents.append(deepcopy(child))
    # Shift original table 2..6 to 3..7 and update all textual cross-references.
    for item in contents:
        ps=[item] if item.tag==f'{{{W}}}p' else item.xpath('.//w:p',namespaces=NS)
        for p in ps:
            old=txt(p); new=re.sub(r'表\s*([2-6])(?!\d)',lambda m:'表'+str(int(m[1])+1),old)
            if old!=new: set_text(p,new)
    def find(prefix):
        return next(x for x in contents if x.tag==f'{{{W}}}p' and txt(x).startswith(prefix))
    set_text(find('本次综述围绕原型已收录文献'),SEARCH_INTRO)
    note=find('注：本表用于比较研究定位')
    idx=contents.index(note)+1
    new=[paragraph('表2 代表性研究的六维证据比较','caption'),
         create_table(MATRIX_HEADERS,MATRIX_ROWS,[1300,1100,1100,1100,1100,1100,1100]),
         paragraph(MATRIX_NOTE),paragraph(SEARCH_GAP)]
    contents[idx:idx]=new
    personal=find('新增模块拟分别落在')
    contents.insert(contents.index(personal)+1,paragraph(CODE_EVIDENCE))
    graph=find('本论文计划新增或实质复核不少于100篇')
    contents.insert(contents.index(graph),paragraph(GRAPH_EVIDENCE))
    set_text(find('该实验已有5,000次'),INTERVAL_EVIDENCE)
    source_head=find('清洗层建立国家或地区')
    contents.insert(contents.index(source_head),paragraph(LICENSE_NOTE))
    function=find('功能测试覆盖采集')
    contents.insert(contents.index(function),paragraph(TEST_EVIDENCE))
    # Replace plans in the source table by the resource-specific findings now available.
    official=next(x for x in contents if x.tag==f'{{{W}}}tbl' and 'WHO FluID' in txt(x))
    target_rows=official.findall(f'{{{W}}}tr')[1:]
    new_license=[
        '已核对WHO数据条款[32]；公共卫生用途附条件使用，修改与第三方权利另核；DON网页不直接套用数据集许可',
        'DATA.GOV.HK条款v1.2[30]；保留政府、数据提供者及门户署名，具体资源例外另核',
        '数据目录明确适用Singapore Open Data Licence v1.0[31]；保留署名、许可链接，不暗示背书',
        '门户迁移且页脚与数据集许可层级不同；资源级许可确认前不新增自动采集或对外分发',
        '已核对ECDC知识产权声明[33]；自有内容、第三方数据库及特定例外分别处理',
    ]
    assert len(target_rows)==len(new_license)
    for row,value in zip(target_rows,new_license):
        cell=row.findall(f'{{{W}}}tc')[1]
        ps=cell.findall(f'{{{W}}}p'); set_text(ps[0],value)
    contents.extend(paragraph(x,'ref') for x in NEW_REFS)
    contents.extend(appendix())
    # Reuse the established school-form grouping to keep each nested table whole.
    groups=[]; narrative=[]; i=0
    while i<len(contents):
        item=contents[i]
        if i+1<len(contents) and contents[i+1].tag==f'{{{W}}}tbl':
            lead=[]
            while narrative and narrative[-1].tag==f'{{{W}}}p':
                style=narrative[-1].find('w:pPr/w:pStyle',NS)
                if style is None or style.get(f'{{{W}}}val') not in {STYLES['h1'],STYLES['h2'],STYLES['h3']}: break
                lead.insert(0,narrative.pop())
            if narrative: groups.append((False,narrative)); narrative=[]
            end=paragraph('','table'); end.find(f'{{{W}}}pPr').append(w('spacing',line=20,lineRule='exact',before=0,after=0))
            groups.append((True,lead+[item,contents[i+1],end])); i+=2
        else: narrative.append(item); i+=1
    if narrative: groups.append((False,narrative))
    for row in report.findall(f'{{{W}}}tr'): report.remove(row)
    for pos,(keep,elements) in enumerate(groups):
        row=w('tr'); rp=w('trPr')
        if keep: rp.append(w('cantSplit'))
        row.append(rp); cell=w('tc'); cp=deepcopy(cellpr)
        for b in cp.findall(f'{{{W}}}tcBorders'): cp.remove(b)
        borders=w('tcBorders')
        if pos==len(groups)-1: borders.append(w('bottom',val='single',color='000000',sz=12))
        cp.append(borders); cell.append(cp); cell.extend(elements); row.append(cell); report.append(row)
    report.append(review)
    assert structure(report.findall(f'{{{W}}}tr')[-1])==review_original
    parts['word/document.xml']=serialize(doc)
    with ZipFile(OUTPUT,'w',ZIP_DEFLATED) as z:
        for info in infos: z.writestr(info,parts[info.filename])
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==source_hash
    with ZipFile(SOURCE) as a, ZipFile(OUTPUT) as b:
        assert b.testzip() is None
        assert [n for n in a.namelist() if a.read(n)!=b.read(n)]==['word/document.xml']
    final_text=txt(doc)
    assert all(f'[{i}]' in final_text for i in range(1,34))
    assert len(doc.xpath('//m:oMath',namespaces={'m':'http://schemas.openxmlformats.org/officeDocument/2006/math'}))==2
    dump('document_update_audit.json',{'source_sha256':source_hash,'output_sha256':hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        'changed_package_parts':['word/document.xml'],'supervisor_assessment_preserved':True,'references':33,
        'main_comparison_tables':7,'appendix_tables':5,'content_characters':len(final_text)})
    print(json.dumps({'output':str(OUTPUT),'characters':len(final_text)},ensure_ascii=False))

if __name__=='__main__': main()
