# -*- coding: utf-8 -*-
"""生成开题报告完善版 v2：补入独立模型对比实验结果，采用更自然的学术行文与排版。"""
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn

SRC = "开题报告.docx"
DST = "S2025081001-安浩然-开题报告（完善版）.docx"

H1, H2, H3, BODY, REF = "h1", "h2", "h3", "body", "ref"

content = [
(H1, "1、选题依据（研究意义、国内外研究现状等）"),
(H2, "1.1 研究背景与意义"),
(BODY, "传染病疫情始终是威胁全球公共卫生安全的重要因素。新冠疫情的全球大流行尚未远去，登革热、季节性流感、猴痘等疫情又在不同地区反复出现，国际社会对未来可能出现的新发传染病（Disease X）也保持高度警惕。在这一背景下，能否及时获取可靠的疫情数据、并对疫情走势作出有依据的预测，直接影响防控资源的调配效率和决策质量。"),
(BODY, "然而，疫情预测在实际业务中的落地并不像模型研究那样顺利。从数据来源看，疫情数据分散在 WHO、各国疾控中心和卫生部门公报等多个渠道，统计口径、更新频率和时间粒度各不相同，靠人工收集整理不仅效率低，也难以保证数据的完整和一致。从数据质量看，监测数据中存在修订、缺报、口径变更等情况，如果缺少原始数据留存和来源溯源机制，后续分析和预测结论就很难被复核和审计。从建模方法看，传染病预测模型体系庞大，机理模型、统计时序模型、机器学习模型和集成模型各有适用条件，实际工作中选择哪个模型、为什么选这个模型，很大程度上依赖个人经验，缺少可以讲清楚依据的选择机制。此外，预测结果的不确定性、适用范围和证据来源如果不能直观地呈现出来，分析人员也难以进行有效的人工复核。"),
(BODY, "近几年大语言模型的快速发展为解决上述部分问题提供了新的思路。大模型在文献证据检索、信息归纳和工具协调方面表现出较强能力，但已有研究也反复指出，大模型直接生成预测数值存在幻觉、输出不可复现、数据边界不清等问题，与公共卫生决策对可靠性和可审计性的要求之间存在明显矛盾。一个比较务实的定位是：让大模型承担证据组织和候选方案提议的工作，而把数值计算交给可以逐行检查的确定性程序来完成。如何在这一分工下设计系统，是本课题要回答的核心工程问题。"),
(BODY, "基于上述考虑，本课题拟设计并实现一套面向多病种疫情监测的可信智能预测系统。系统一方面建设覆盖受控采集、原始留存、标准化入库、数据画像和可信发布的数据管道，解决数据来源分散和可信性核验困难的问题；另一方面引入预测模型知识图谱，把病种、传播机制、数据特征和文献证据关联起来，为不同国家、病种和指标给出有依据、可解释的模型推荐，并通过执行契约把“模型推荐”和“预测执行”分开管理，保证每一次正式预测都经过授权、可以回溯。研究成果可为公共卫生分析人员提供一个可追溯、可审计、可解释的疫情监测与预测辅助工具，对规范人工智能技术在公共卫生领域的应用也具有一定的参考价值。"),
(H2, "1.2 国内外研究现状"),
(H3, "（1）传染病预测模型研究现状"),
(BODY, "传染病预测模型大体可以分为四类。机理模型方面，Kermack 和 McKendrick 提出的房室模型奠定了传染病动力学研究的理论基础[1]，此后基于个体的仿真模型（如 Covasim）进一步刻画了人群异质性和干预措施的影响[2]，类似的建模思路也被用于登革热传播与疫苗效果评估[3]。统计时序与机器学习方面，多尺度流感预测模型[4]和面向不同空间分辨率的神经网络方法[5]都取得了较好的预测效果，贝叶斯平滑类方法在实时疫情跟踪中表现出较强的通用性[6]。集成方法是近年来的热点，Tsang 等提出的自适应权重集成方法在香港流感数据上明显优于单一模型[7]，美国 FluSight 平台的评估结果同样说明集成预测可以降低对单一模型的依赖[8]。综合这些研究可以看出，不存在在所有病种、所有数据条件下都最优的模型，模型选择必须结合具体的数据特征和预测场景，这一判断构成了本课题的出发点之一。"),
(H3, "（2）人工智能与大模型在疫情监测中的应用现状"),
(BODY, "人工智能技术在传染病监测中的应用已有较多积累，Brownstein 等对相关进展作了系统总结[9]，Wong 等则从更宽的角度讨论了人工智能在传染病防治中的作用[22]。大语言模型出现以后，van Hoek 等论证了将大模型智能体整合进疫情分析流水线的价值和投入必要性[10]，Du 等尝试利用大模型改进实时传染病预测[11]，面向传染病的基座模型构想也被正式提出[12]。与此同时，针对大模型局限性的研究同样值得关注：Hager 等评估了大模型在临床决策中的问题并讨论了缓解措施[13]，Mehandru 等对临床场景下的大模型智能体进行了系统评估[14]，结论都指向同一点——大模型的灵活推理能力有价值，但不受约束的生成和不透明的工具调用难以直接用于严肃的决策支持。Syrowatka 等在综述中也提示，弱溯源和弱本地控制可能放大已有的不平等[23]。把大模型定位为受约束的协调者而非直接的预测者，正逐渐成为可信疫情智能系统设计的共识方向。"),
(H3, "（3）知识图谱与证据溯源研究现状"),
(BODY, "知识图谱在生物医学领域的应用相对成熟。Chandak 等构建了面向精准医疗的知识图谱，支持证据驱动的研究决策[15]；面向生物医学证据探索与综合的深度知识图谱研究展示了图谱在证据组织方面的潜力[16]；知识图谱嵌入和图学习方法也被广泛用于生物医学关系推理[17][18]。这些工作说明，用图谱组织“证据—结论”关系是可行的。但已有图谱研究大多面向药物、基因等对象，专门针对疫情预测模型选择、并且与预测执行治理联动的图谱研究目前还比较少，这正是本课题可以切入的空间。"),
(H3, "（4）预测评估与可信治理研究现状"),
(BODY, "以数据为中心的疫情预测研究强调对数据来源、质量和使用场景的显式记录[19]；FAIR-AI 框架为医疗人工智能的实施和评审给出了可操作的流程[20]；Youssef 等则提出，健康领域 AI 模型的一次性外部验证应当被持续的本地化验证所取代[21]。在评估设计层面，历史回测、一次性盲测和前瞻性部署所能支持的结论强度是不同的，同一个误差指标在不同评估设计下含义并不相同，如果对评估身份不加区分，回顾性结果很容易被误当作新鲜验证来呈现。Ye 等在人工智能与机理模型结合的综述中也强调了评估规范的重要性[24]。这些研究提示我们，疫情预测系统不仅要“算得准”，还要“说得清”，评估过程和评估身份的管理应当成为系统设计的一部分。"),
(H3, "（5）研究现状小结"),
(BODY, "总的来看，疫情预测模型体系已经比较丰富，但模型选择仍然缺少可解释、可审计的依据；大模型为证据组织提供了新能力，但直接使用难以满足可信要求；数据治理和评估治理研究各自都有进展，却较少有工作把可信数据管道、知识图谱模型推荐、授权化预测执行和人工复核可视化贯通为一个完整系统。本课题正是针对这一空白开展研究。"),
(H2, "1.3 前期工作基础"),
(BODY, "课题申请人依托北京市科学技术情报研究所的相关工作，围绕本课题已完成了系统原型建设和多组实证实验，为论文研究打下了比较好的基础。"),
(BODY, "在数据与知识资产方面，已构建覆盖 WHO、CDC 等官方来源的受控数据采集与标准化入库链路，实现原始记录留存、来源标识、检索时间和内容哈希的全程溯源；建成预测模型知识图谱，当前快照包含 1,574 个文献全文版本、4,502 条证据片段、3,791 条结构化断言和 26 个国家—病种—指标—频率数据画像，并生成 17 份默认挂起的执行契约。在大模型受约束应用方面，完成了基于本地部署 Qwen3-4B 模型的证据检索与参数提议实验，全部数值计算均由确定性代码完成。在案例验证方面，以巴西登革热周报数据（2024 年 1 月至 2026 年 7 月，共 74 个观测）为例，实现了从实时观测、数据画像、图谱推荐到授权门槛的完整可追溯链路。"),
(BODY, "在模型对比实验方面，已取得两组较为重要的阶段性结果。第一组是与 Tsang 等发表于 Nature Communications 的香港流感自适应权重集成方法（AWBE）的对比[7]：项目独立训练的递归季节性梯度提升模型不使用论文算法的任何输出，在 1,104 组配对观测上，短预测期（H0–H1）WAPE 为 30.43%，与论文方法的 30.22% 相差 0.21 个百分点，达到预设的 1 个百分点相当性标准，RMSE（2.5868）略优于论文（2.6230），其中 H0 单步预测 WAPE 为 24.56%，优于论文的 25.98%；同时也如实确认了 H2–H8 长预测期上的差距，并在 4,968 组配对观测上完成了验证锁定的预测期路由实验（主测试期 WAPE 33.94%，对照 34.18%）。第二组是与 Bleichrodt 等发表于 Royal Society Open Science 的时间序列预测方法对比[26]：在新加坡登革热周报数据（2012–2019 年，194 组配对观测，1–4 周预测期）上，项目组合模型 WAPE 为 22.89%，与论文最优方法 ARIMA 的 22.31% 相差 0.59 个百分点，处于预设的 3 个百分点可比范围内；更值得注意的是区间质量，项目方法的 95% 预测区间覆盖率为 90.7%–96.9%，明显高于各论文方法（17%–80.4%），说明系统在不确定性量化方面具有优势。"),
(BODY, "需要说明的是，上述实验均为回顾性配对基准而非新鲜盲测，新加坡登革热 2020–2022 年时间外推测试中项目方法未能保持相当性，多病种稳定泛化、前瞻性验证等问题仍有待深入研究。这些已有成果和尚存的问题，共同构成了本论文的研究起点。"),
(H1, "2、选题的研究目标、研究内容、研究方法以及拟解决的关键问题等"),
(H2, "2.1 研究目标"),
(BODY, "本课题的总体目标是：面向多病种疫情监测的实际需求，设计并实现一套本地优先、可审计、可解释的可信智能预测系统，覆盖从多源数据采集、可信数据治理、知识图谱辅助模型选择、授权化预测执行到可视化人工复核的各个环节，为公共卫生分析人员提供可追溯的疫情监测与预测辅助工具。具体包括四个方面的目标：一是建成覆盖 WHO、CDC 等官方来源的合规采集与可信数据管道，实现原始留存、标准化和全程溯源；二是建成预测模型知识图谱和可解释的模型推荐机制，能够说明推荐理由和适用边界；三是建立推荐与执行解耦的授权治理机制和评估身份管理机制，保证评估结论客观、不夸大；四是实现疫情趋势、预警、来源覆盖和模型置信度的可视化展示，并通过多病种实证检验系统的实际效果。"),
(H2, "2.2 研究内容"),
(H3, "（1）多源疫情数据受控采集与原始留存"),
(BODY, "针对数据来源分散的问题，研究遵守 robots.txt、站点条款和访问频率限制的合规采集方法，设计面向 WHO、CDC 等官方数据目录的来源连接器和采集代理；研究原始响应的不可变归档机制，在解析之前固化原始数据，记录来源标识、检索时间和内容哈希，为后续溯源和审计提供依据。"),
(H3, "（2）疫情数据清洗、标准化与可信数据管道"),
(BODY, "针对多源异构数据的治理问题，研究疫情数据的模式规范化方法，将国家、病种、指标、单位、频率、起止时间、报告日期和来源版本映射为统一的数据身份；研究基于校验规则的去重、异常标记和缺失记录机制；设计数据画像器，自动计算观测数、缺失区间、修订记录、过离散程度、趋势、离群点和候选变点等特征，为模型选择提供数据侧依据；构建可信库、预览库和预测库分离的分层存储，未经复核的数据不进入预测流程。"),
(H3, "（3）预测模型知识图谱构建与可解释模型选择"),
(BODY, "针对模型选择缺乏依据的问题，研究面向疫情预测的“文献版本—证据片段—结构化断言—模型推荐”图谱本体和版本化构建方法，证据片段携带页码或偏移定位、节选文本和哈希值，保证每条结论可回溯到原文；研究基于语义准入规则和多维评分的模型推荐方法，从数据结构适配、病种机制适配、场景迁移、科学证据、参数置信度和执行就绪度六个维度对候选模型打分，每一分都能对应到具体规则和证据；研究本地部署大模型在允许清单约束下的证据检索与参数提议机制，发挥大模型的证据组织能力，同时把数值计算排除在其职责之外。"),
(H3, "（4）预测执行授权、评估治理与反馈学习"),
(BODY, "针对预测过程不可审计的问题，研究执行契约机制，把数据画像身份、图谱版本、模型参数、预测原点、风险标记和授权状态显式绑定，推荐默认处于挂起状态，经授权后方可执行；研究留出样本注册表和评估身份管理方法，区分历史回放、影子评估、一次性盲测和前瞻性评估，防止测试集被重复使用而导致结论失真；研究回测流程、WAPE/MAE/RMSE 指标计算、预测区间生成与可靠性校准方法；研究反馈台账机制，让成熟的评估结果能够修正后续推荐，当简单基线表现更好时允许其替代图谱推荐，保证系统尊重实证证据。"),
(H3, "（5）可视化系统与多病种实证"),
(BODY, "针对结果展示和复核不直观的问题，研究面向分析人员的可视化界面设计，呈现疫情趋势、预警信息、来源覆盖、模型置信度和预测不确定性区间，支持人工复核和备注；基于 Docker Compose 完成本地一体化部署；选取巴西登革热、香港流感、新加坡登革热、奥地利军团病等公开监测序列开展多病种实证，与已发表方法进行配对比较，并通过消融实验分析知识图谱、执行契约、反馈台账等各组件的实际贡献。"),
(H2, "2.3 研究方法"),
(BODY, "本课题综合采用以下研究方法。文献研究法：系统调研传染病预测模型、疫情监测人工智能应用、知识图谱和可信 AI 治理等领域的文献，梳理模型体系和评估规范，为系统设计和图谱本体提供依据。软件工程方法：采用模块化、接口显式的工程化设计和原型迭代开发，将网络访问、文件写入、数据库操作等副作用集中在系统边界，配合单元测试、集成测试和容器化冒烟测试保证质量。知识图谱构建方法：采用版本化、不可变的图谱发布机制，结合人工审核断言和证据哈希溯源，保证知识资产可信、可复算。实验研究法：采用冻结数据、锁定预测原点的回测设计，以 WAPE 为主要指标、MAE 和 RMSE 为辅助指标，与已发表方法在相同目标集上配对比较，通过消融实验、时间外推测试和一次性盲测评估泛化能力，所有评估结果均标注评估身份。案例研究法：选取典型的“国家—病种—指标”序列进行端到端追踪，检验从实时观测到授权预测的完整流程。"),
(H2, "2.4 拟解决的关键问题"),
(BODY, "本课题拟重点解决四个关键问题。第一，多源异构疫情数据的可信对齐与全程溯源，即如何处理不同来源在口径、频率、单位上的差异，以及数据修订、缺失情形下的可信入库和审计问题。第二，病种—数据—模型匹配的可解释推荐，即在不存在通用最优模型的前提下，如何依据数据画像和文献证据给出可回溯、可复算的模型推荐及其适用边界。第三，推荐与执行解耦的授权治理，即大模型参与分析决策时，如何防范幻觉和越权执行风险，确保数值计算只由确定性代码在显式授权后完成。第四，评估身份与泛化证据管理，即如何区分回顾性结果与新鲜盲测结果，防止结论被夸大，并客观界定系统的能力边界——前期实验中时间外推测试的失利已经说明，这一问题不是形式要求，而是真实存在的风险。"),
(H1, "3、论文提纲（标至三级标题）"),
(BODY, "第1章 绪论"),
(BODY, "1.1 研究背景与意义"),
(BODY, "1.2 国内外研究现状"),
(BODY, "1.2.1 传染病预测模型研究现状"),
(BODY, "1.2.2 人工智能与大模型在疫情监测中的应用现状"),
(BODY, "1.2.3 知识图谱与可信评估治理研究现状"),
(BODY, "1.3 研究内容与技术路线"),
(BODY, "1.4 论文组织结构"),
(BODY, "第2章 相关理论与关键技术"),
(BODY, "2.1 传染病时间序列预测方法"),
(BODY, "2.1.1 机理模型与统计时序模型"),
(BODY, "2.1.2 机器学习与集成预测模型"),
(BODY, "2.2 知识图谱构建与证据溯源技术"),
(BODY, "2.3 大语言模型受约束协调机制"),
(BODY, "2.4 预测评估指标与不确定性量化"),
(BODY, "2.5 本章小结"),
(BODY, "第3章 系统需求分析与总体设计"),
(BODY, "3.1 业务场景与需求分析"),
(BODY, "3.1.1 功能性需求"),
(BODY, "3.1.2 可信性与合规性需求"),
(BODY, "3.2 系统总体架构设计"),
(BODY, "3.2.1 分层架构与模块划分"),
(BODY, "3.2.2 数据流与身份管理设计"),
(BODY, "3.3 数据库与存储设计"),
(BODY, "3.4 本章小结"),
(BODY, "第4章 可信数据管道与知识图谱模型选择的设计与实现"),
(BODY, "4.1 多源数据受控采集与原始留存实现"),
(BODY, "4.1.1 来源连接器与合规采集"),
(BODY, "4.1.2 不可变归档与溯源元数据"),
(BODY, "4.2 数据清洗、标准化与数据画像实现"),
(BODY, "4.2.1 模式规范化与去重校验"),
(BODY, "4.2.2 数据画像与质量标记"),
(BODY, "4.3 预测模型知识图谱构建实现"),
(BODY, "4.3.1 图谱本体与版本化发布"),
(BODY, "4.3.2 证据片段与结构化断言管理"),
(BODY, "4.4 基于知识图谱的模型推荐实现"),
(BODY, "4.4.1 语义准入与多维评分"),
(BODY, "4.4.2 大模型受约束提议机制"),
(BODY, "4.5 本章小结"),
(BODY, "第5章 授权化预测执行、评估治理与可视化实现"),
(BODY, "5.1 执行契约与授权机制实现"),
(BODY, "5.2 回测、评估身份与留出注册表实现"),
(BODY, "5.2.1 回测流程与指标计算"),
(BODY, "5.2.2 评估身份分类与盲测管理"),
(BODY, "5.3 反馈台账与推荐修正机制"),
(BODY, "5.4 可视化界面与人工复核实现"),
(BODY, "5.5 系统集成与容器化部署"),
(BODY, "5.6 本章小结"),
(BODY, "第6章 系统实验与结果分析"),
(BODY, "6.1 实验环境与数据集"),
(BODY, "6.2 巴西登革热端到端推荐案例"),
(BODY, "6.3 香港流感基准对比实验"),
(BODY, "6.3.1 短预测期独立模型相当性比较"),
(BODY, "6.3.2 预测期路由与全预测期比较"),
(BODY, "6.3.3 时间外推泛化检验"),
(BODY, "6.4 新加坡登革热方法迁移对比实验"),
(BODY, "6.5 多病种回测与消融实验"),
(BODY, "6.6 结果讨论与局限性分析"),
(BODY, "6.7 本章小结"),
(BODY, "第7章 总结与展望"),
(BODY, "7.1 研究工作总结"),
(BODY, "7.2 不足与未来展望"),
(BODY, "参考文献"),
(BODY, "致谢"),
(H1, "4、论文研究进度计划、预期研究结果"),
(H2, "4.1 研究进度计划"),
(BODY, "2026年9月—2026年10月：完成文献调研和需求分析，完善开题报告，明确系统总体技术路线和论文框架。"),
(BODY, "2026年10月—2026年12月：完成多源数据受控采集、原始留存和标准化入库链路的实现与测试，建成可信数据管道和数据画像模块，扩展国家—病种—指标数据画像的覆盖范围。"),
(BODY, "2027年1月—2027年2月：完成预测模型知识图谱的版本化构建和大模型受约束提议机制的实现，完善模型推荐评分与执行契约授权流程。"),
(BODY, "2027年2月—2027年3月：完成回测评估、留出注册表、反馈台账和可视化界面的实现，完成 Docker Compose 一体化部署与集成测试。"),
(BODY, "2027年3月—2027年4月：开展多病种实证实验，完成基准对比、消融和时间外推泛化分析，整理实验数据与图表。"),
(BODY, "2027年4月—2027年5月：完成学位论文初稿，提交导师审阅并修改完善。"),
(BODY, "2027年5月—2027年6月：完成论文定稿、查重、盲审和答辩准备。"),
(H2, "4.2 预期研究结果"),
(BODY, "（1）完成一套本地优先、可审计的面向多病种疫情监测的可信智能预测系统，实现合规采集、可信数据管道、知识图谱模型推荐、授权化预测执行和可视化人工复核功能，并通过容器化部署验证。前期原型已实现主要功能，本课题将在此基础上完善和规范化。"),
(BODY, "（2）建成版本化的预测模型知识图谱和配套证据溯源资产，形成可解释的模型推荐与执行契约机制，在现有 1,574 个文献版本、3,791 条断言的快照基础上进一步扩充。"),
(BODY, "（3）完成多病种实证评估。前期实验已表明，系统独立模型在香港流感短预测期上与 Nature Communications 发表的自适应权重集成方法总体相当（WAPE 30.43% 对 30.22%），在新加坡登革热数据上与已发表方法可比（WAPE 相差 0.59 个百分点）且预测区间覆盖率明显更优；本课题将进一步完成多病种回测、消融实验和时间外推检验，明确系统在不同数据条件下的适用边界，所有预测结果均附带不确定性区间和评估身份标识。"),
(BODY, "（4）完成硕士学位论文一篇，争取发表学术论文一篇或申请软件著作权一项，形成可复现的代码、数据清单和实验记录。"),
(H1, "5、参考文献"),
(REF, "[1] KERMACK W O, MCKENDRICK A G. A contribution to the mathematical theory of epidemics[J]. Proceedings of the Royal Society of London. Series A, 1927, 115: 700-721."),
(REF, "[2] KERR C C, STUART R M, MISTRY D, et al. Covasim: an agent-based model of COVID-19 dynamics and interventions[J]. PLoS Computational Biology, 2021, 17(7): e1009149."),
(REF, "[3] PERKINS T A, PAZ-SOLDAN V A, STODDARD S T, et al. An agent-based model of dengue virus transmission shows how uncertainty about breakthrough infections influences vaccination impact projections[J]. PLoS Computational Biology, 2019, 15(3): e1006710."),
(REF, "[4] OSTHUS D, MORAN K R. Multiscale influenza forecasting[J]. Nature Communications, 2021, 12: 2991."),
(REF, "[5] AIKEN E L, NGUYEN A T, VIBOUD C, et al. Toward the use of neural networks for influenza prediction at multiple spatial resolutions[J]. Science Advances, 2021, 7(37): eabb1237."),
(REF, "[6] MCGOUGH S F, JOHANSSON M A, LIPSITCH M, et al. Nowcasting by Bayesian smoothing: a flexible, generalizable model for real-time epidemic tracking[J]. PLoS Computational Biology, 2020, 16(10): e1007735."),
(REF, "[7] TSANG T K, DU Q, COWLING B J, et al. An adaptive weight ensemble approach to forecast influenza activity in an irregular seasonality context[J]. Nature Communications, 2024, 15: 8625."),
(REF, "[8] MATHIS S M, WEBBER C, LEÓN T M, et al. Evaluation of FluSight influenza forecasting in the 2021-22 and 2022-23 seasons with a new target laboratory-confirmed influenza hospitalizations[J]. Nature Communications, 2024, 15: 6289."),
(REF, "[9] BROWNSTEIN J S, RADER B, ASTLEY C M, et al. Advances in artificial intelligence for infectious-disease surveillance[J]. New England Journal of Medicine, 2023, 388(17): 1597-1607."),
(REF, "[10] VAN HOEK A J, PRIES B, KRETZSCHMAR M E E, et al. Importance of investing time and money in integrating large language model-based agents into outbreak analytics pipelines[J]. The Lancet Microbe, 2024, 5(10): 100881."),
(REF, "[11] DU H, LAGRAVE D, RATNAM L, et al. Advancing real-time infectious disease forecasting using large language models[J]. Nature Computational Science, 2025, 5: 467-480."),
(REF, "[12] LAU M S Y, METCALF C J E, LIU Z, et al. Toward AI foundation models for epidemics: promise, challenges, and paths forward[J]. Proceedings of the National Academy of Sciences, 2026, 123: e2526192123."),
(REF, "[13] HAGER P, JUNGMANN F, HOLLAND R, et al. Evaluation and mitigation of the limitations of large language models in clinical decision-making[J]. Nature Medicine, 2024, 30: 2613-2622."),
(REF, "[14] MEHANDRU N, MIAO B Y, ALMARAZ E R, et al. Evaluating large language models as agents in the clinic[J]. npj Digital Medicine, 2024, 7: 84."),
(REF, "[15] CHANDAK P, HUANG K, ZITNIK M. Building a knowledge graph to enable precision medicine[J]. Scientific Data, 2023, 10: 67."),
(REF, "[16] WANG Z, CHEN Z, YANG Z, et al. Empowering biomedical evidence exploration and synthesis with deep knowledge graph research[J]. Nature Machine Intelligence, 2026, 8: 1142-1156."),
(REF, "[17] FERNÁNDEZ-TORRAS A, DURAN-FRIGOLA M, BERTONI M, et al. Integrating and formatting biomedical data as pre-calculated knowledge graph embeddings in the Bioteque[J]. Nature Communications, 2022, 13: 5304."),
(REF, "[18] BANG D, LIM S, LEE S, et al. Biomedical knowledge graph learning for drug repurposing by extending guilt-by-association to multiple layers[J]. Nature Communications, 2023, 14: 3570."),
(REF, "[19] RODRÍGUEZ A, KAMARAJU H, BUI T, et al. Machine learning for data-centric epidemic forecasting[J]. Nature Machine Intelligence, 2024, 6: 1122-1131."),
(REF, "[20] WELLS B J, GRAHAM K N, LEE H, et al. A practical framework for appropriate implementation and review of artificial intelligence (FAIR-AI) in healthcare[J]. npj Digital Medicine, 2025, 8: 514."),
(REF, "[21] YOUSSEF A, PENCINA M, THAKUR A, et al. External validation of AI models in health should be replaced with recurring local validation[J]. Nature Medicine, 2023, 29: 2686-2687."),
(REF, "[22] WONG F, DE LA FUENTE-NUNEZ C, COLLINS J J. Leveraging artificial intelligence in the fight against infectious diseases[J]. Science, 2023, 381(6654): 164-170."),
(REF, "[23] SYROWATKA A, KUZNETSOVA M, ALSUBAI A, et al. Leveraging artificial intelligence for pandemic preparedness and response: a scoping review to identify key use cases[J]. npj Digital Medicine, 2021, 4: 96."),
(REF, "[24] YE Y, ZHANG Y, WANG Y, et al. Integrating artificial intelligence with mechanistic epidemiological modeling: a scoping review of opportunities and challenges[J]. Nature Communications, 2025, 16: 581."),
(REF, "[25] ROSTER K, CONNAUGHTON C, RODRIGUES F A. Machine-learning-based forecasting of dengue fever in Brazilian cities using epidemiologic and meteorological variables[J]. American Journal of Epidemiology, 2022, 191(10): 1803-1812."),
(REF, "[26] BLEICHRODT A, LUO R, KIRPICH A, et al. Evaluating the forecasting performance of ensemble sub-epidemic frameworks and other time series models for the 2022-2023 mpox epidemic[J]. Royal Society Open Science, 2024, 11(7): 240248."),
]

doc = Document(SRC)
cell = doc.tables[1].rows[0].cells[0]

for p in list(cell.paragraphs):
    p._element.getparent().remove(p._element)

def set_font(run, size=10.5, bold=False, east="宋体"):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "Times New Roman"
    run.font.color.rgb = RGBColor(0, 0, 0)
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = rPr.makeelement(qn('w:rFonts'), {})
        rPr.append(rFonts)
    rFonts.set(qn('w:eastAsia'), east)

def add_para(text, kind):
    p = cell.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    run = p.add_run(text)
    if kind == H1:
        set_font(run, size=14, bold=True, east="黑体")
        pf.space_before = Pt(12)
        pf.space_after = Pt(6)
    elif kind == H2:
        set_font(run, size=12, bold=True, east="黑体")
        pf.space_before = Pt(6)
        pf.space_after = Pt(3)
    elif kind == H3:
        set_font(run, size=10.5, bold=True, east="黑体")
        pf.space_before = Pt(3)
        pf.space_after = Pt(0)
    elif kind == REF:
        set_font(run, size=10.5)
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.left_indent = Pt(21)
        pf.first_line_indent = Pt(-21)  # 悬挂缩进
    else:
        set_font(run, size=10.5)
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.first_line_indent = Pt(21)
    return p

for kind, text in content:
    add_para(text, kind)

doc.save(DST)
total = sum(len(t) for _, t in content)
print("已保存:", DST)
print("正文总字符数:", total)
