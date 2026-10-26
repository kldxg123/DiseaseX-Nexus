from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from copy import deepcopy
import re,json,hashlib
from lxml import etree as E
from build_proposal_revision_20261022 import paragraph,create_table,w,W,NS,serialize,STYLES
R=Path(__file__).resolve().parents[1]; O=R/'output/proposal_submission_20261023';O.mkdir(exist_ok=True)
S=Path('F:/微信聊天/xwechat_files/wxid_hmoavx3grubj22_8875/temp/RWTemp/2026-10/809eeb85455e208eef7937d1d45cc860/安浩然_开题报告_余乐批注审核版.docx')
def text(p):return ''.join(p.xpath('.//w:t/text()',namespaces=NS))
def put(p,s):
 pr=deepcopy(p.find(f'{{{W}}}pPr'))
 for a in list(p):p.remove(a)
 if pr is not None:p.append(pr)
 r=w('r');t=w('t');t.text=s;r.append(t);p.append(r)
with ZipFile(S) as z:parts={n:z.read(n) for n in z.namelist()}
d=E.fromstring(parts['word/document.xml']);comments=E.fromstring(parts['word/comments.xml'])
review=[]
for c in comments:review.append({'id':c.get(f'{{{W}}}id'),'comment':text(c),'status':'已补充方案或收紧结论；涉及签字、三病种独立数据及实际新实验的事项未冒充完成'})
ps=d.xpath('//w:p',namespaces=NS)
def find(s):return next(p for p in ps if text(p).startswith(s))
put(find('多病种疫情监测需要持续获取'),'公共卫生分析通常经历通报下载、指标核对、数据整理、趋势研判和人工复核。官方病例数可能在后续发布中修订，流感样病例就诊率、实验室阳性率与确诊病例数也不能直接互换。香港Flu Express按周提供多项监测指标，新加坡历史周报则同时记录多种疾病[22-23]；分析人员需要逐项确认病种、地区、统计周和计量单位，再判断能否用于同一预测任务。来源更新与口径变化增加了复核负担，错误合并或把缺报填成零，还可能使预警方向发生偏差。因此，本课题首先解决数据能否追溯、模型为何适用及结果是否经过审核的问题。')
put(find('现有机理模型、统计模型'),'本课题的应用意义是减少分析人员在来源核对和重复选模上的工作，使每个预测结果能够追查其输入、模型和审核状态。工程上，需要将采集、推荐、运行与发布的权限分开，并在数据不足时停止数值预测。方法上，拟检验知识图谱约束是否在相同候选集与预算下改善模型选择，而不是将接入更多模型视为性能提升。')
put(find('大语言模型能够参与资料检索'),'大语言模型在本系统中承担任务解释、证据整理和候选参数提议，不直接决定数据真伪或发布预警。这是根据系统风险所作的设计选择，其效果将与不使用大语言模型的确定性流程比较。')
abstract=next(p for p in ps if text(p).startswith('本研究') or text(p).startswith('本课题') and len(text(p))>200 and ps.index(p)<62)
put(abstract,'本课题面向公开疫情资料整理、模型选择与人工复核工作，研究知识图谱辅助的多病种监测与预测系统。在既有原型基础上，拟独立完成可复算的候选模型评分、输入版本与授权对象绑定、测试隔离及策略更新记录。系统保存来源快照、推荐依据和审核状态；数据不足时返回补数或拒绝原因。研究以固定候选集和资源预算开展对照与消融，分别评价推荐命中率、计算成本、WAPE及WIS，并测试过期授权、参数错配与重复请求的拦截情况。已有香港流感和新加坡登革热结果仅作为回顾性研究基础，保留长预测期退化与时间外推失败。当前尚无三种病种均满足许可、有效长度和独立测试要求的材料，因此跨病种泛化仅作为探索目标，不声称对未知病原具有可靠预测能力。预期交付可本地运行的系统、本人新增模块差分及可复核实验材料。')
find('国内研究也在探索').addprevious(paragraph('国内应用研究主要关注本地监测资料与网络信息的结合，国际研究则较多依托预测平台和统一提交协议开展多团队评价。两类工作分别提供特征迁移和标准化评估经验，但均需结合本课题的数据条件重新验证。','p'))
find('国内研究也在探索').addprevious(paragraph('国内应用进展','h3'))
find('房室模型为描述').addprevious(paragraph('国际预测研究与应用','h3'))
find('国内团队提出的呼吸道').addprevious(paragraph('TimesFM以分块输入和解码器式预训练支持不同长度、预测期与频率的时序任务，其ICML论文报告了跨数据集零样本预测结果[34]。Chronos将数值缩放、量化后转为离散序列，在多类数据上训练概率预测模型[35]。这两项工作可作为预训练时序模型的比较方向，但其跨领域结果不等于本课题已具备跨病种泛化能力；接入时还需核对预训练数据重叠、最低输入条件和预算。'))
put(find('本课题拟在现有原型基础上'),'本课题设定三个可验收目标。第一，形成保留原始来源、统计口径、版本和复核状态的数据管道，以抽样追溯和异常记录检查其完整性。第二，实现证据约束的模型选择，在固定候选池和预算下与仅画像规则等基线比较推荐质量与成本。第三，建立授权运行和隔离评估流程，检查身份错配、过期及重复请求能否被拦截。跨病种预测仅在各病种独立数据达到准入要求后开展，未达到时报告覆盖不足，不以治理演示替代预测验证。')
put(find('RQ3以留一病种组'),'RQ3作为探索性问题，按留一病种、留一地区与锁定后时间窗口分别分析。当前尚不满足三病种独立预测评价条件，不预设覆盖成功比例。取得第三病种的资源级许可、有效长度和冻结协议后，再登记跨病种实验；此前仅报告两病种历史结果及稀疏任务的拒绝行为。各病种、预测期分别报告WAPE、WIS和基线差值，不将数据接入或小图谱请求通过率解释为预测泛化证据。')
# Insert detailed mechanisms before the thesis outline; retain the school form.
extra=[]
def p(s,k='p'):extra.append(paragraph(s,k))
def tab(title,heads,rows,widths):p(title,'caption');extra.append(create_table(heads,rows,widths))
p('2.6 关键机制的验证约束','h2')
p('指标原理与实现分开说明。WAPE按绝对误差之和除以真实值绝对值之和计算；分母为零时不定义，不加任意小常数。不同单位与病例规模的序列分别报告，再按病种汇总，不能直接拼接。WIS使用同一分位数网格0.025、0.10、0.25、0.50、0.75、0.90、0.975，对应50%、80%、95%中心区间。区间评分先计宽度，再按2/α惩罚越界距离；WIS把中位数绝对误差乘0.5、各区间评分乘α/2后求和，再除以3.5[17]。该定义在论文第2章集中介绍，具体预测与校准流程放在实现章节。')
p('六维评分的可复算示例为：D=0.8、M=1、T=0.5、E=1、P=0.5、R=1，等权时适配分为80分。此例只检验计算过程，不是实测推荐准确率。硬准入失败的候选不参与排序，即使软分数较高也不执行。开发侧预先比较最低观测数8/16/26、季节历史104/156周、滞后可用率70%/80%/90%、展示阈值40/50/60及候选数1/3/5，记录拒绝率、预测损失与成本；最终测试前选择并锁定一套规则。不得按最终测试表现选择阈值。')
tab('表8 授权状态转移与故障测试设计',['当前状态及事件','迁移或处理','预期检查'],[
['待审核，人工确认身份及版本','进入已批准并登记有效期','审核者有权限，载荷哈希与审批对象一致'],['已批准，版本与参数未变','进入执行中，完成后记为已执行','同一幂等键最多产生一次正式结果'],['已批准，但过期或已撤销','转为失效，拒绝运行','不能由普通操作者重新激活'],['参数、快照或图谱版本改变','原批准失效，重新审核','改变任何绑定字段均不得沿用批准'],['执行中断或重放请求','标记失败或返回已有执行状态','不重复训练，不将半成品作为正式输出']],[2700,2500,2700])
p('状态转移由服务端校验，界面隐藏按钮不代替权限控制。采集操作者可提交候选数据，分析人员可提出模型任务，具有审批权限的审核者决定执行和发布，审计角色只读查看记录。测试矩阵覆盖无授权、跨角色、过期、撤销、快照替换、模型参数变更和重放；分别登记合法与非法用例的输入、预期状态和实际结果。表8是待实现或扩展的验证设计，不代表200项状态机测试已经通过。')
p('资源预算同时限制拟合次数、墙钟时间与模型调用次数，逐任务记录CPU/GPU使用时间、峰值内存和调用费用；不同硬件的时间不折算成未经标定的统一算力。初版在同一硬件环境设5/15/30分钟三个墙钟预算档，并保留原30次拟合、5次提议作为上限。所有策略使用相同候选集、目标键和中止规则，超时、失败及无预测均计入完成率与成本。固定规则、随机选择、仅画像、无图谱、无大模型及有限搜索分别对照，以区分推荐机制、候选池和额外计算的影响。')
p('知识图谱抽样以冻结的去重作品及断言ID为样本框，按病种、模型族和证据类型分层。层内用登记的随机种子无放回抽样，分层抽样估计按层规模加权；各层至少抽取样本并公开纳入概率。审核者分别填写原文是否支持、条件是否匹配和来源是否可追溯，分歧由第三人裁决；尚未确定的复核人不虚填姓名。比例置信区间使用Wilson方法，双人样本另报一致率与κ。修正后的质量复查使用未抽过的样本，并保留首次错误，不用重审同批样本代替独立复查。')
tab('表9 当前病种资料与独立验证状态',['任务','已知可用材料','尚缺条件及结论'],[
['香港流感','已有1,104个H0—H1配对目标；不是1,104周','已查看历史结果，H0单列；尚未登记新独立测试期'],['新加坡登革热','冻结412周；另有2020—2022年157周已查看外推','旧外推劣于AR(2)，保留失败；新窗口未冻结'],['巴西登革热','既有画像74条有效观测、覆盖约131周','仍为同一病种；许可与长度不足，不作为第三病种'],['军团病','官方资料获取与治理候选','有效长度、资源级许可和冻结日期未齐，不列入已完成预测'],['埃博拉、尼帕','稀疏事件及图谱请求资料','仅检查拒绝、补数和追溯；不转换为虚构周序列']],[1700,3000,3200])
p('目前可用于既有定量结果讨论的病种只有流感和登革热，尚不能验证三病种预测泛化。系统范围保留多病种任务支持，论文核心结论限定为证据约束选模与授权评估机制，跨病种泛化作为探索性分析；第三病种只有在资源许可、序列长度和独立窗口登记齐备后才纳入。412周按60%/20%/20%划分约为247/82/83周，但各阶段还须扣除训练窗、特征滞后与预测期，不能把83周直接等同于83个有效原点。每病种在冻结前提交可达性计算表，达不到26个有效原点时单列小样本，不反复改变分割。')
p('历史公开序列可能已修订，文献知识也可能晚于预测时点。因此现有结果标注为事后修订数据回测；知识图谱须按证据发表日期和系统收录日期作时间截断，不能用后来文献解释当时可用的推荐能力。香港分别报告H0及H1—H8，未取得逐预测期的配对区间前不重述总体0.21个百分点为前瞻预测优势。新加坡外推失败可能与分布变化、季节特征失配或策略选择有关，这些是待检验解释；通过训练侧漂移诊断与固定基线消融区分，不据现有结果断言具体原因。')
p('影子评价拟于2026年11月1日前完成策略与输入注册，最迟2026年11月8日启动12个真实周历的连续观察，计划于2027年1月31日前结案；这些是拟定日期，不是已执行冻结。官方缺报或来源中断按缺失事件登记，不补造成绩，也不顺延后挑选有利窗口；少于12个有效发布周时报告实际长度和未达目标。用户评价预先提供来源核对、缺报发现、区间解释和错配推荐四类任务脚本，定义错误引用、遗漏缺报、误读区间及误批准为操作错误；5—8人的结果只支持探索性可用性，不代表疾控部门采纳或预测有效性。')
p('个人贡献按模块、接口、修改前后文件哈希、差分、测试日志和负责人确认登记。当前没有可用Git提交历史，不能倒填提交或把全部既有代码归给本人。既有资产清单与选定测试日志随脱敏证据包提供；未签署的贡献归属、未完成的双人质量审核及新增病种独立数据仍列为待确认事项。后续论文评价使用可核查新增差分和独立实验，而不是645个文件或981个静态函数的数量。')
target=find('3 论文提纲');cell=target.getparent();idx=list(cell).index(target);tail=list(cell)[idx:]
for x in tail:cell.remove(x)
report=cell.getparent().getparent();cp=deepcopy(cell.find(f'{{{W}}}tcPr'));anchor=cell.getparent();pos=list(report).index(anchor)+1
groups=[];buf=[]
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
 row.append(rp);c=w('tc');c.append(deepcopy(cp));c.extend(els);row.append(c);report.insert(pos,row);pos+=1
# Teacher's separately appended review is preserved in the response file, not in student prose.
body=d.find(f'{{{W}}}body');deleting=False;teacher=[]
for x in list(body):
 if '安浩然开题报告综合审核意见' in text(x):deleting=True
 if deleting and x.tag!=f'{{{W}}}sectPr':teacher.append(text(x));body.remove(x)
for x in list(body)[::-1]:
 if x.tag==f'{{{W}}}sectPr':continue
 if x.tag==f'{{{W}}}p' and not text(x).strip():body.remove(x)
 else:break
# Add two verified research references, then renumber by first textual appearance.
refps=[p for p in d.xpath('//w:p',namespaces=NS) if re.match(r'^\[\d+\] ',text(p))]
refs={int(re.match(r'^\[(\d+)\]',text(p))[1]):text(p) for p in refps}
refs[34]='[34] DAS A, KONG W, SEN R, et al. A decoder-only foundation model for time-series forecasting[C/OL]//Proceedings of the 41st International Conference on Machine Learning. PMLR, 2024, 235: 10148-10167[2026-10-23]. https://proceedings.mlr.press/v235/das24c.html.'
refs[35]='[35] ANSARI A F, STELLA L, TURKMEN C, et al. Chronos: Learning the language of time series[J/OL]. Transactions on Machine Learning Research, 2024[2026-10-23]. https://arxiv.org/abs/2403.07815. DOI:10.48550/arXiv.2403.07815.'
refset=set(refps);order=[];pat=re.compile(r'\[(\d+(?:\s*[-–,，]\s*\d+)*)\]')
def numbers(s):
 out=[]
 for a in re.split('[,，]',s):
  q=re.split('[-–]',a);out.extend(range(int(q[0]),int(q[-1])+1))
 return out
for p0 in d.xpath('//w:p',namespaces=NS):
 if p0 in refset:continue
 for m0 in pat.finditer(text(p0)):
  for n in numbers(m0[1]):
   if n in refs and n not in order:order.append(n)
order+= [n for n in refs if n not in order];mapping={old:i+1 for i,old in enumerate(order)}
for p0 in d.xpath('//w:p',namespaces=NS):
 if p0 in refset:continue
 old=text(p0);new=pat.sub(lambda m:'['+','.join(str(mapping.get(n,n)) for n in numbers(m[1]))+']',old)
 if old!=new:put(p0,new)
parent=refps[0].getparent();pos=list(parent).index(refps[0])
for x in refps:x.getparent().remove(x)
for old in order:
 s=re.sub(r'^\[\d+\]',f'[{mapping[old]}]',refs[old]);parent.insert(pos,paragraph(s,'ref'));pos+=1
for x in d.xpath('//w:commentRangeStart|//w:commentRangeEnd|//w:commentReference',namespaces=NS):x.getparent().remove(x)
parts['word/document.xml']=serialize(d)
for n in list(parts):
 if re.fullmatch('word/comments[^/]*.xml',n):del parts[n]
for n in ['word/_rels/document.xml.rels','[Content_Types].xml']:
 root=E.fromstring(parts[n])
 for x in list(root):
  if 'comments' in str(x.attrib).lower():root.remove(x)
 parts[n]=serialize(root)
dest=O/'安浩然_开题报告_导师审核后修订稿_20261023.docx'
with ZipFile(dest,'w',ZIP_DEFLATED) as z:
 for n,b in parts.items():z.writestr(n,b)
(O/'批注处理与提交须知.txt').write_text('本稿按36条批注调整，保留导师原评语。\n不可代签、不可编造：个人贡献确认、第三病种独立数据、双人质量审核及新实验尚待完成。\n新增状态机测试矩阵、预算与阈值敏感性、WIS定义及评分样例是研究设计，不冒充已完成结果。\n附录数据与18项离线测试证据来自2026-10-22包，不因本轮文字修改改变验证身份。\n参考文献按首次出现重新编号，新增TimesFM、Chronos两篇；网络访问日不作为发表年。\n\n老师综合意见原文\n'+'\n'.join(teacher)+'\n\n逐条批注\n'+'\n'.join(str(x['id'])+' '+x['comment']+'\n处理：'+x['status'] for x in review),encoding='utf-8')
print(dest)
