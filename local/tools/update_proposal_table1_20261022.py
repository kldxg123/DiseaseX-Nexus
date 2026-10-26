"""Update only table 1 and its directly related explanatory wording."""
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
from lxml import etree as E
from build_proposal_revision_20261022 import create_table, paragraph, NS, W, serialize

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'output/proposal_revision_20261022'
SOURCE = WORK / '安浩然_开题报告_依据批注修订完整版_20261022.docx'
OUTPUT = WORK / '安浩然_开题报告_表一研究定位更新版_20261022.docx'

def text(node):
    return ''.join(node.xpath('.//w:t/text()', namespaces=NS))

def set_text(node, value):
    pr = deepcopy(node.find(f'{{{W}}}pPr'))
    first = node.find(f'{{{W}}}r')
    rp = deepcopy(first.find(f'{{{W}}}rPr')) if first is not None else None
    for child in list(node):
        node.remove(child)
    if pr is not None:
        node.append(pr)
    run = E.SubElement(node, f'{{{W}}}r')
    if rp is not None:
        run.append(rp)
    E.SubElement(run, f'{{{W}}}t').text = value

source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
with ZipFile(SOURCE) as z:
    infos = z.infolist()
    parts = {i.filename: z.read(i.filename) for i in infos}
doc = E.fromstring(parts['word/document.xml'])
before = deepcopy(doc)
ps = doc.xpath('//w:p', namespaces=NS)
caption = next(p for p in ps if text(p) == '表1 代表性工作的研究重点比较')
old_table = caption.getnext()
assert old_table.tag == f'{{{W}}}tbl'
assert len(old_table.findall(f'{{{W}}}tr')) == 8
headers = ['代表性研究', '研究对象', '主要研究重点', '本课题借鉴内容', '本课题拟进一步研究的内容']
rows = [
    ['香港流感集成预测[4]', '流感监测与预测', '多类预测方法集成及效果评价', '集成预测与回顾性验证方法', '依据不同病种的数据条件与预测目标，研究模型适配和选择'],
    ['FluSight[5]', '多团队流感预测', '统一预测任务、提交规范与评价', '标准化预测输出和统一评价流程', '将规范化评价接入本地系统，形成可重复的运行与审核流程'],
    ['PandemicLLM[9]', '多模态疫情预测', '利用政策、基因组与时序等信息开展预测', '多模态信息辅助疫情建模的思路', '研究大语言模型与独立预测模型的协作，以及推荐依据的可追溯性'],
    ['PrimeKG[12]', '生物医学知识组织', '整合多源生物医学实体与关系', '知识组织与来源关联方法', '建立病种、数据特征、预测方法与适用条件之间的关联'],
    ['FAIR-AI[13]', '人工智能应用治理', '实施前评价与实施后监测', '生命周期治理与审查机制', '将人工审核、操作授权和运行记录落实为可测试的系统功能'],
    ['呼吸道传染病智能体框架[20]', '疫情智能分析架构', '连接监测、风险评价、预警与决策支持', '多模块协作与人工监督的架构思路', '在明确任务与数据范围内实现原型，并验证预测和工作流效果'],
    ['本课题（拟研究）', '多病种监测与辅助预测', '整合数据获取、知识辅助选模、预测与审核', '综合上述相关研究', '验证模型适配、多病种迁移和人工审核机制的有效性，明确适用边界'],
]
replacement = create_table(headers, rows, [1350, 1050, 1650, 1550, 2300])
old_table.getparent().replace(old_table, replacement)
set_text(caption, '表1 代表性研究与本课题的研究定位比较')
note = next(p for p in ps if text(p).startswith('表中“非主线”表示'))
set_text(note, '注：本表用于比较研究定位，不用于比较预测性能。各项概括限于所引文献明确报告的研究内容，不能据此认定相关系统缺少表中未列出的功能。“本课题（拟研究）”及最后一列表示拟开展的研究，不代表已完成实现或效果验证。预测精度、多病种泛化能力与运行效率将在后续对照实验中分别评价。')
# Keep the adjacent explanation consistent with the purpose of the new table.
intro = next(p for p in ps if text(p).startswith('本次综述围绕原型已收录文献'))
set_text(intro, text(intro) + '表1按研究对象、主要研究重点、可借鉴内容和本课题拟进一步研究的内容进行比较，用于说明本课题与已有研究的联系及研究定位。')
human = next(p for p in ps if '并强调人在环监督[20]' in text(p))
set_text(human, text(human).replace('并强调人在环监督[20]', '并强调由人参与监督与审核[20]'))
heading = next(p for p in ps if text(p) == '2.2.6 可视化与人在环复核')
set_text(heading, '2.2.6 可视化与人工复核')
final_xml = serialize(doc)

# Verify all changes are restricted to the intended table and five paragraphs.
before_ps = before.xpath('//w:p', namespaces=NS)
changed_old = [p for p in before_ps if text(p) in {
    '表1 代表性工作的研究重点比较',
    '2.2.6 可视化与人在环复核',
} or text(p).startswith(('表中“非主线”表示', '本次综述围绕原型已收录文献')) or '并强调人在环监督[20]' in text(p)]
assert len(changed_old) == 5
baseline_table = next(t for t in before.xpath('//w:tbl', namespaces=NS) if t.xpath('./w:tr/w:tc/w:p/w:r/w:t[text()="代表工作"]', namespaces=NS))
baseline_table.getparent().remove(baseline_table)
replacement.getparent().remove(replacement)
for p in changed_old:
    p.getparent().remove(p)
changed_new = [caption, note, intro, human, heading]
for p in changed_new:
    p.getparent().remove(p)
assert E.tostring(before) == E.tostring(doc), 'Unexpected changes outside the approved slots'
with ZipFile(SOURCE) as z:
    assert z.testzip() is None
parts['word/document.xml'] = final_xml
with ZipFile(OUTPUT, 'w', ZIP_DEFLATED) as z:
    for info in infos:
        z.writestr(info, parts[info.filename])
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == source_hash
with ZipFile(SOURCE) as a, ZipFile(OUTPUT) as b:
    assert b.testzip() is None
    changed_parts = [n for n in a.namelist() if a.read(n) != b.read(n)]
    assert changed_parts == ['word/document.xml']
print(json.dumps({'output': str(OUTPUT), 'changed_parts': changed_parts, 'table_rows': 8, 'table_columns': 5, 'paragraphs_updated': 5}, ensure_ascii=False, indent=2))
