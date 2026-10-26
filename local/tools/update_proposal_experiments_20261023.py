from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from copy import deepcopy
import json,hashlib
from lxml import etree as E
from build_proposal_revision_20261022 import W,NS,w,paragraph,create_table,serialize
R=Path(__file__).resolve().parents[1];D=R/'reports/three_disease_chronological_20261023';O=R/'output/proposal_submission_20261023'
S=O/'安浩然_开题报告_导师审核后修订稿_20261023.docx';OUT=O/'安浩然_开题报告_三病种新增实验与中文润色稿_20261023.docx'
def txt(p):return ''.join(p.xpath('.//w:t/text()',namespaces=NS))
def put(p,s):
 pr=deepcopy(p.find(f'{{{W}}}pPr'))
 for x in list(p):p.remove(x)
 if pr is not None:p.append(pr)
 run=w('r');t=w('t');t.text=s;run.append(t);p.append(run)
with ZipFile(S) as z:parts={n:z.read(n) for n in z.namelist()}
d=E.fromstring(parts['word/document.xml']);ps=d.xpath('//w:p',namespaces=NS)
edits=[]
for p in ps:
 s=txt(p);n=s
 if s.startswith('本课题面向公开疫情资料整理'):
  n=s.replace('当前尚无三种病种均满足许可、有效长度和独立测试要求的材料，因此跨病种泛化仅作为探索目标，不声称对未知病原具有可靠预测能力。','新增登革热、弯曲菌肠炎与腮腺炎的时间外推实验，按病种报告结果；优化方案在查看首轮结果后提出，仅作为探索证据，不声称已完成前瞻盲测或未知病原预测验证。')
 if s.startswith('目前可用于既有定量结果讨论的病种只有'):
  n='新增实验已补充新加坡弯曲菌肠炎和腮腺炎序列，与登革热构成三个病种的同源回顾评价，各含574周观测。旧有香港与新加坡实验仍按原身份保留。第三病种的时间外推结果可用于讨论数据条件变化，但不等于未经接触的独立盲测；同一国家与监测系统的数据也不足以证明跨地区泛化。新增实验的划分、方法和结果见2.7节。'
 if s.startswith('RQ3作为探索性问题'):
  n='RQ3按病种、地区和时间窗口分别分析。新增三病种回顾实验检验冻结方法与滚动选模在后续年份的表现，跨地区与未知病原预测仍为探索目标。各病种、预测期分别报告WAPE、WIS和基线差值，不将数据接入或图谱请求通过率解释为预测泛化证据。后续仍需锁定后新增数据评价，不能用已查看的历史测试替代。'
 if s.startswith('已完成的是定向检索留档'):
  n=s.replace('图谱断言的双人标注与抽样准确率、锁定后新病例窗口、至少三种病种的无泄漏对照、200项授权状态机测试','本次另完成三病种分时段回顾对照，结果与限制见2.7节。图谱断言的双人标注与抽样准确率、锁定后新病例窗口、三病种新的独立验证、200项授权状态机测试')
 if s.startswith('本课题拟回答三个主要研究问题'):
  n=s.replace('推荐策略在跨病种、跨地区和时间变化条件下能否维持相对基线的有效性','推荐策略在不同病种和时间变化条件下能否维持相对基线的有效性，并探索跨地区适用条件')
 if n!=s:put(p,n);edits.append({'before':s,'after':n})
metrics=json.loads((D/'metrics.json').read_text());adaptive=json.loads((D/'adaptive_metrics.json').read_text());names={'Dengue Fever':'登革热','Campylobacterenterosis':'弯曲菌肠炎','Mumps':'腮腺炎'}
extra=[paragraph('2.7 新增三病种回顾实验','h2'),paragraph('为检查模型选择在不同病种上的适用性，本次新增新加坡卫生部周报中的弯曲菌肠炎与腮腺炎，与登革热构成同源三病种评价[2]。三个序列均从2012-W01至2022-W52，各有574周，单位为每周报告病例数。来源按Singapore Open Data Licence v1.0署名使用[32]，保留官方接口响应、取得时刻及SHA-256。弯曲菌肠炎在2017年前后分别标记为Campylobacterenterosis和Campylobacter enteritis；解析器按这两个名称映射同一任务，并检查周键唯一性和逐年连续性。首次单名称解析未覆盖完整时期而被校验拒绝，修复记录与原始标签同时保存，映射仍需分析人员复核。'),
paragraph('训练期固定为2012—2017年，2018年按1至4周合并WAPE选择候选，2019年仅用于各模型、各预测期的有符号残差校准，2020—2022年用于回顾评价。候选为持续性、4周均值、52周季节性及对数尺度AR(2)，不采用DiseaseX方案，不调用大语言模型。每个原点只读取此前已公布序列，跨分区目标不混入选模或校准；测试期允许按实际观测逐周扩展历史，但不重新校准区间。每病种包含157个预测原点，H1至H4分别为157、156、155和154个目标键，合计622个，不能将这些相关目标键当作622个独立周。'),
paragraph('首轮在2018年选出的模型均为对数AR(2)，其后两个病种的WAPE劣于4周均值。根据这一结果增加滚动选模：每个预测原点在历史末段13个验证原点上比较四个固定候选，以已观察到的1至4周绝对误差之和选模，再预测后续目标。选模窗口不读取当前原点以后的观测，区间沿用对应候选的2019年校准残差。该优化是在查看首轮结果后形成，属于探索性再分析；没有将原测试集重新声明为盲测。滚动选择与完整候选搜索使用相同候选池，额外计算成本单独记录，不据此宣称节省搜索成本或取得知识图谱增益。')]
rows=[]
for disease,label in names.items():
 for model,name in [('log_ar2','首轮固定选择'),('mean4','4周均值'),('last_value','持续性')]:
  r=next(x for x in metrics if x['disease']==disease and x['model']==model and x['horizon']=='all')
  rows.append([label,name,f"{r['wape']*100:.2f}%",f"{r['wis']:.3f}",f"{r['coverage95']*100:.2f}%"])
 r=next(x for x in adaptive if x['disease']==disease and x['horizon']=='all');rows.append([label,'滚动选择（探索）',f"{r['wape']*100:.2f}%",f"{r['wis']:.3f}",f"{r['coverage95']*100:.2f}%"])
extra +=[paragraph('表10 新增三病种2020—2022年回顾评价','caption'),create_table(['病种','方法','WAPE','WIS','95%覆盖率'],rows,[1500,2300,1300,1300,1500]),paragraph('滚动选择的WAPE在登革热、弯曲菌肠炎和腮腺炎上分别为22.09%、28.96%和46.70%。其在后两个病种上较首轮固定选择降低误差，但与4周均值的28.95%和46.75%接近；登革热则略差于固定选择的21.97%。该结果支持根据近期历史比较简单候选的必要性，尚未证明滚动选择整体优于简单基线。三种疾病的WIS数值受病例规模影响，不直接相互排序。登革热95%区间覆盖率仅为75.72%，提示2019年残差校准未适应后续波动；保留该校准不足，不在测试集上扩大区间后重报成绩。当前仅给出描述性差异，未作显著性、相当性或非劣结论。'),paragraph('新增模块实现输入验证、季节性准入、滚动选模、七分位数输出与WIS计算，并通过12项选定离线测试，覆盖零分母、缺失和负值拒绝、重复计算一致性、输入不变、区间顺序、手算评分及目标键数量。完整逐预测期指标、预测分位数、协议、源码与测试日志另附。新增三病种材料完善了回顾评价覆盖，尚不能替代新时段的独立验证、授权状态机测试或真人任务评价。')]
anchor=next(p for p in ps if txt(p)=='3 论文提纲');cell=anchor.getparent();parent=cell.getparent().getparent();at=list(cell).index(anchor);tail=list(cell)[at:]
for item in extra:
 paras=[item] if item.tag==f'{{{W}}}p' else item.xpath('.//w:p',namespaces=NS)
 for para in paras:
  pr=para.find(f'{{{W}}}pPr');st=pr.find(f'{{{W}}}pStyle').get(f'{{{W}}}val')
  pr.append(w('spacing',line=240,lineRule='auto'))
  if st=='ProposalBody':pr.append(w('ind',firstLine=420))
  for run in para.findall(f'{{{W}}}r'):
   rp=run.find(f'{{{W}}}rPr')
   if rp is None:rp=w('rPr');run.insert(0,rp)
   rp.extend([w('rFonts',ascii='Times New Roman',hAnsi='Times New Roman',eastAsia='黑体' if st=='ProposalHeading2' else '宋体'),w('sz',val=24 if st=='ProposalHeading2' else 21),w('color',val='000000')])
for x in tail:cell.remove(x)
cp=deepcopy(cell.find(f'{{{W}}}tcPr'));pos=list(parent).index(cell.getparent())+1;groups=[];buf=[]
for x in extra+tail:
 if x.tag==f'{{{W}}}tbl':
  cap=buf.pop()
  if buf:groups.append((False,buf));buf=[]
  groups.append((True,[cap,x,paragraph('')]))
 else:buf.append(x)
if buf:groups.append((False,buf))
for keep,els in groups:
 row=w('tr');rp=w('trPr')
 if keep:rp.append(w('cantSplit'))
 row.append(rp);c=w('tc');c.append(deepcopy(cp));c.extend(els);row.append(c);parent.insert(pos,row);pos+=1
for p in d.xpath('//w:p',namespaces=NS):
 s=txt(p);new=s.replace('每个原点只读取此前已公布序列','每个原点只读取该位置之前的历史记录；资料取自当前存档版本，不保证等同当时发布值').replace('额外计算成本单独记录，不据此宣称节省搜索成本或取得知识图谱增益','额外计算成本仍需与固定单模型分别核算，不据此宣称节省搜索成本或取得知识图谱增益')
 if new!=s:put(p,new)
parts['word/document.xml']=serialize(d)
with ZipFile(OUT,'w',ZIP_DEFLATED) as z:
 for n,b in parts.items():z.writestr(n,b)
notice='修改说明\n一、删除无实际信息的表达：合并重复的“未达目标不得宣称泛化”限制，将其集中到新增实验与结论附近。\n二、逻辑与结构：原有数字、引文和表号保留；新增实验单列2.7节及表10，分别说明数据、流程、结果及限制。将此前“没有第三病种数据”的现状更新为三病种回顾数据已齐，但独立新窗口仍未完成。\n三、待补证据：两种英文标签的人工复核、知识图谱增量贡献、区间覆盖不足的开发侧校准、配对差值区间、新时段独立验证及个人贡献签字仍需补充。\n原有香港与新加坡失败结果未删除；本次滚动优化不能称为盲测结果。'
(O/'新增实验修改说明.txt').write_text(notice,encoding='utf-8')
bundle=O/'三病种实验_代码数据与复核材料_20261023.zip'
with ZipFile(bundle,'w',ZIP_DEFLATED) as z:
 for p in D.glob('*'):
  if p.is_file():z.write(p,'experiment/'+p.name)
 for p in [R/'packages/predictor_agent/chronological_validation.py',R/'tools/run_three_disease_20261023.py',R/'tools/finish_three_disease_20261023.py']:
  z.write(p,p.relative_to(R).as_posix())
 z.write(O/'新增实验修改说明.txt','修改说明.txt')
print(OUT)
