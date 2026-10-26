# ruff: noqa: E501
from __future__ import annotations

SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "开场、摘要与研究问题",
        [
            (
                "标题与开场",
                "DiseaseX Nexus: Self-improving LLM agents for adaptive infectious-disease forecasting",
                "各位老师好，我汇报的工作是 DiseaseX Nexus。它面向的不是某一种固定传染病，而是病原体、监测口径和数据可用性不断变化的真实环境。这里的“自学习”不等于模型会自动变得更准，而是系统能够保留历史经验、失败记录和评价身份，再据此提出下一轮受约束的模型配置。",
            ),
            (
                "摘要段",
                "Forecasting systems for infectious diseases must adapt...",
                "这篇论文解决两个同时存在的问题：一方面，多病种预测需要根据数据特征动态选择模型；另一方面，这种动态选择必须可追溯、可复现、可拒绝执行。系统用 LLM 做证据检索和配置建议，用知识图谱连接病种、数据画像、模型和参数来源，再由确定性代码完成训练和评价。香港流感主测试中，路由器 WAPE 为 33.94%，与 AWBE 的 34.18%接近；但后疫情外部窗口变为 49.35%对48.88%，优势没有保持。因此论文强调的是可信适应框架，而不是稳定领先。",
            ),
            (
                "关键词段",
                "Keywords: infectious-disease forecasting...",
                "关键词可以概括为六个方面：传染病预测、LLM 智能体、知识图谱、模型选择、可审计性和前瞻评价。其中前四项说明系统怎样适应不同病种，后两项说明我们怎样限制过度结论。",
            ),
        ],
    ),
    (
        "引言：为什么需要多病种自适应",
        [
            (
                "引言第1段：问题不稳定",
                "Effective epidemic intelligence requires both timely observations...",
                "传染病预测不是一次性建模。病原体、生物学特征、人群免疫、干预、气候、流动和报告延迟都会变化。因此同一模型即使在一个阶段有效，也可能在另一个病种或时期失效。系统必须同时回答“为什么选这个模型”“当时用了什么信息”以及“这个结果属于哪种评价”。",
            ),
            (
                "引言第2段：模型不存在统一排名",
                "Infectious-disease modelling encompasses mechanistic...",
                "现有方法从机理模型、个体仿真到统计模型、机器学习和集成都有适用场景。病例数、阳性率、累计发生率和事件率的语义不同，数据长度和频率也不同，所以不存在脱离数据条件的统一最优模型。多病种自适应首先是“按语义和条件选模型”，而不是扩大搜索规模。",
            ),
            (
                "引言第3段：LLM 的安全角色",
                "LLMs provide a complementary capability...",
                "LLM 擅长阅读文献、整理约束和协调工具，但不能直接充当数值预测器。我们的分工是：LLM 负责提出候选方案，真正的拟合、回测、指标计算和数据库写入全部由确定性代码完成。这样既保留了跨病种理解能力，又避免幻觉直接进入预测结果。",
            ),
            (
                "引言第4段：评价身份",
                "A second gap concerns evaluation...",
                "同一个 WAPE 数值，如果来自历史回放、反复查看的测试集、一次性盲测或真实前瞻部署，能支持的结论完全不同。因此本系统把标签是否暴露、测试是否已经消费、结果是否推广等信息与指标一起保存。泛化能力不仅看分数，还看分数是怎样得到的。",
            ),
            (
                "引言第5段：总体方案",
                "We developed DiseaseX Nexus to address these linked challenges...",
                "整体流程是：数据智能体获取合规来源，数据画像建立带时间边界的身份，知识图谱把证据连接到病种—模型关系，R3C 对模型和参数进行门控，执行合同决定是否允许运行，成熟结果再写回反馈账本。这个闭环使系统可以跨病种调整，但不会覆盖旧决策或失败记录。",
            ),
            (
                "图1：系统架构",
                "Fig. 1 DiseaseX Nexus system architecture",
                "看图时从左到右讲。左侧是经过批准的数据和文献，中间是可信数据层、知识图谱和 R3C，右侧是执行合同与确定性模型。最关键的是执行前的授权、泄漏和身份检查，以及执行后的人工复核与反馈回流。跨病种适应发生在画像和推荐层，数值计算仍然是可复现的。",
            ),
            (
                "引言收束：论文验证什么",
                "Here, we evaluate the claims that the current implementation can support...",
                "论文按证据强度组织实验：先验证知识资产和安全门控，再做巴西登革热的非执行案例，之后用香港流感做已执行基准，最后检查多个病种和评价身份。需要提前说明，现有证据支持可行性、可审计性和错误控制，但不支持普遍的多病种高精度或稳定外部优势。",
            ),
        ],
    ),
    (
        "结果一：可信架构与知识图谱",
        [
            (
                "结果第1段：五层架构",
                "DiseaseX Nexus comprises five operational layers...",
                "系统包含数据获取、可信处理、图谱推理、执行授权和确定性建模五层。每条数据保留来源、抓取时间和内容哈希，画像记录频率、缺失、修订、趋势和预测起点。不同病种进入系统后，先被转换为可比较的数据身份，再决定哪些模型族和参数来源可以进入候选集合。",
            ),
            (
                "结果第2段：执行合同",
                "The execution contract defined the decisive boundary...",
                "推荐完成并不等于允许预测。即使候选模型、参数和证据都齐全，合同仍可以保持 DEFERRED_NOT_EXECUTED。只有数据身份、图谱版本、预测起点、特征范围、盲测状态和回滚条件全部满足，系统才执行。对于高风险公共卫生场景，“不生成预测”有时就是正确结果。",
            ),
            (
                "结果第3段：知识资产规模",
                "The audited snapshot contained 1,574 unique full-text versions...",
                "当前快照包括1574个全文版本、4502个证据片段和3791条结构化断言。证据片段有页码或偏移定位和双重哈希，断言把病种、模型族、指标语义和适用条件连接起来。26个数据画像代表不同的国家—病种—指标—频率身份，这正是多病种自适应的知识基础。",
            ),
            (
                "结果第4段：所有合同保持延迟执行",
                "All 17 contracts in this snapshot retained the status DEFERRED_NOT_EXECUTED...",
                "17份合同全部处于延迟执行状态，并不是模型失败，而是本次快照只验证了推荐层，没有授权预测层。这个结果说明系统能够把“建议”和“执行”分开，也提醒我们当前知识资产仍是经过核验的静态快照，不能描述成持续在线更新的生产系统。",
            ),
            (
                "图2：证据到合同的链路",
                "Fig. 2 Knowledge-graph provenance and executable recommendation assets",
                "这张图展示从文档版本、证据片段、病种—模型断言、R3C 推荐、参数包到执行合同的完整链路。讲这张图时要强调，系统的自适应不是黑盒换模型，而是每次选择都有证据、版本和参数来源，可以被人工追问和复现。",
            ),
        ],
    ),
    (
        "结果二：巴西登革热的质量感知适应",
        [
            (
                "登革热第1段：数据画像",
                "We used the Brazil–dengue–new weekly cases identity...",
                "巴西登革热案例有74个周观测，存在强过度离散、趋势、离群点、疑似变化点和406天的长缺口。系统没有把缺口自动补零，也没有使用预测起点之后的数据。这个案例说明，换到新病种时，系统先识别数据生成机制和质量风险，再决定模型，而不是套用流感配置。",
            ),
            (
                "登革热第2段：语义门控与排序",
                "After semantic gating, R3C ranked six non-baseline count-model candidates...",
                "因为目标是新增病例数，系统保留计数时间序列、状态空间和更新模型，排除需要分母的比例或事件率模型。hurdle count 模型排名第一，但有些高证据分模型因为参数置信度不足仍不能入选。这里的分数代表证据充分性，不代表预测准确率。",
            ),
            (
                "登革热第3段：有推荐但不制造结果",
                "The selected recommendation included a complete provenance record...",
                "最后推荐对象和参数包都生成了，但合同没有授权，因此没有拟合、回测和预测值。这个负结果非常重要：系统可以完成跨病种模型推理，同时拒绝在证据或授权不足时制造性能。",
            ),
            (
                "图3：数据质量审计",
                "Fig. 3 Trusted Brazil dengue observations and data-quality audit",
                "图中上部显示病例和死亡序列，中部明确标出时间缺口和修订，下部显示预测边界与人工审核。我要强调的是，图里的数据质量问题不是被隐藏或插补掉，而是直接参与模型选择和执行门控，这就是质量感知的自适应。",
            ),
        ],
    ),
    (
        "结果三：香港流感对比试验与四张新图",
        [
            (
                "香港流感第1段：评价设计",
                "We next evaluated an executed benchmark...",
                "这里进入真正执行的预测基准。主测试覆盖4968个共同的起点—预测期—目标组合，预测期为0到8周，并排除已知监测制度变化期；外部测试是2023至2024年的423行后疫情数据。所有模型都在相同目标键上比较，从而避免样本不一致。",
            ),
            (
                "香港流感第2段：独立模型与路由器必须分开",
                "The paper models were evaluated using immutable row-level predictions...",
                "DiseaseX 在开发期评价450种编排配置，得到独立集成模型。另一个路由规则在0周使用 DiseaseX，在1到8周直接使用论文 AWBE。因此路由器可以说明“编排能否贴近论文方法”，但不能作为不依赖论文方法的 DiseaseX 独立结果。汇报时必须把两者分开。",
            ),
            (
                "香港流感第3段：WAPE 主结果",
                "On the primary 4,968-row test...",
                "主测试中，独立 DiseaseX 把基线 WAPE 从75.94%降到50.27%，说明自适应集成优于固定常数基线，但仍高于 AWBE 的34.18%。路由器为33.94%，仅比 AWBE 低0.24个百分点；外部窗口则为49.35%对48.88%，反而高0.47个百分点。结论是主期接近，但没有外部确认。",
            ),
            (
                "新补充图1：误差分布与逐行排名",
                "Supplementary Fig. 1 Main-test error distribution and accuracy-grade landscape",
                "左图比较四种方法的逐行绝对误差分布，右图统计每一行预测中最好、中间和最差的次数。这比只报一个 RMSE 更直观：我们既能看到平均水平，也能看到模型是否只在少数样本上获益。图中同时标明路由器依赖 AWBE，避免把组合结果误说成独立模型能力。",
            ),
            (
                "新补充图2：DiseaseX 与 AWBE 的配对效应",
                "Supplementary Fig. 2 Paired evidence synthesis",
                "横轴是 DiseaseX 独立集成减去 AWBE 的 WAPE。只有0周预测为负值，说明 DiseaseX 在即时预测上略好；从1周开始差距逐步扩大，到长预测期明显落后。置信区间和年度异质性说明这个差异并不稳定，因此需要按预测期和年份评价，而不能只看总体均值。",
            ),
            (
                "新补充图3：主测试模型—预测期全景",
                "Supplementary Fig. 3 Main-test model–horizon performance landscape",
                "这张六面板图把模型、模型族和0到8周预测期放在同一视图。核心信息是：不同模型在不同预测期的优势不同，模型族的平均表现也会随预测距离变化。它从实验上支持“按数据画像和预测期路由”的必要性，而不是使用一个固定模型覆盖全部任务。",
            ),
            (
                "新补充图4：后疫情外部泛化",
                "Supplementary Fig. 4 Post-COVID external generalization landscape",
                "外部窗口保留了冻结配置，不再调参。图中按模型族、预测带和误差分布展示性能。虽然多数方法相对常数基线仍有改善，但 DiseaseX 独立模型和路由器都没有建立对 AWBE 的稳定优势。这张图是论文关于泛化能力最重要的证据，也是我们主动限制结论的依据。",
            ),
            (
                "香港流感第4段：治理状态",
                "The experiment identity was RETROSPECTIVE_AUTHOR_OUTPUT_BENCHMARK...",
                "这项实验被登记为作者输出的回顾性基准，状态是仅影子运行、未推广。系统验证了预测重放和未来标签哨兵，但没有数据库写入、正式预测运行或活动模型切换。也就是说，我们可以分析泛化，但不会把回顾性比较直接升级成公共卫生产品。",
            ),
        ],
    ),
    (
        "结果四：评价身份、反馈学习与多病种实验",
        [
            (
                "评价身份第1段：多条轨道不能混报",
                "Four additional result identities illustrate the importance of metric provenance...",
                "奥地利军团菌病和芬兰汉坦病毒的 WAPE 分别为22.7%和28.0%，但属于历史或重复窗口；香港流感也是作者输出基准。这些结果可以说明流程能运行，却不能当作新的盲测证据。跨病种泛化必须同时报告病种、频率、窗口和评价身份。",
            ),
            (
                "评价身份第2段：失败盲测被保留",
                "Track C, weekly Chile influenza, was the only fresh blind test...",
                "智利流感是唯一一次性新鲜盲测，WAPE 为71.7%，没有达到30%的门槛。系统保留失败并拒绝认证，而且更换模型编号也不能恢复“新鲜”状态。这个例子证明自学习系统不会通过反复试验和重新命名来美化结果。",
            ),
            (
                "反馈第1段：经验可以推翻知识推荐",
                "Two shadow demonstrations tested whether empirical feedback could override...",
                "在奥地利军团菌病和秘鲁流感上，知识图谱推荐的复杂模型都输给简单基线，系统因此记录基线覆盖并把成熟结果写回反馈账本。自适应不是坚持图谱推荐，而是允许真实经验修正知识。",
            ),
            (
                "反馈第2段：受控调优不等于成功",
                "A controlled Qwen-assisted tuning experiment provides more limited evidence...",
                "Qwen 在允许范围内提出三轮配置，验证 WAPE 从0.6501下降到0.5836，隐藏测试相对滚动均值改善13.07%，但仍没有通过门槛。因此“自学习”表示存在记忆—建议—评价—反馈机制，不表示未见数据上的误差会单调下降。",
            ),
            (
                "反馈第3段：伪盲筛选仍不推广",
                "Finally, three candidate influenza sequences were screened...",
                "泰国、澳大利亚和墨西哥流感的历史伪盲 WAPE 都高于30%，系统保留“不推广”决定，也没有查看真正的末端盲测标签。这里体现的是适应过程受预算和新鲜度约束，而不是不断试到满意为止。",
            ),
            (
                "多病种回顾性实验",
                "A frozen-data evaluation was completed for monthly Austrian legionellosis...",
                "在奥地利军团菌病、芬兰汉坦病毒和巴西登革热三种不同病种、不同时间频率的数据上，修订后的回顾性 WAPE 都低于30%，宏观 WAPE 也优于四个组件消融。但这些改进发生在查看初版测试结果之后，因此只能说明系统能跨病种调整，不能作为新鲜泛化证据；巴西登革热的 RMSE 仍输给末值基线，也必须保留。",
            ),
        ],
    ),
    (
        "讨论：怎样准确表述自适应与泛化",
        [
            (
                "讨论第1段：重新定义 LLM 角色",
                "DiseaseX Nexus reframes the role of an LLM...",
                "本研究把 LLM 从数值预测器改成受约束的协调器。灵活性来自证据检索和配置提议，可靠性来自版本图谱、执行合同和确定性引擎。这个设计使多病种模型选择可以被质疑、复核和撤销。",
            ),
            (
                "讨论第2段：当前最强证据",
                "The strongest current evidence concerns provenance and error containment...",
                "目前最有力的证据是来源追踪和错误控制，而不是预测精度。系统能把推荐追溯到证据，在没有授权时停止执行，保留失败盲测，并允许基线推翻图谱推荐。这些特性降低了自适应系统选择性报告的风险。",
            ),
            (
                "讨论第3段：香港流感的校准结论",
                "The Hong Kong influenza benchmark provides a calibrated assessment...",
                "独立 DiseaseX 明显优于常数基线，但没有接近 AWBE；路由器主期只领先0.24个百分点，外部期反而落后0.47个百分点。由此要区分两种泛化：系统机制可以迁移到新窗口，但某个预测配置的性能未必能迁移。论文只支持前者，尚未证明后者。",
            ),
            (
                "讨论第4段：数据异质性",
                "Data heterogeneity remains the principal obstacle...",
                "不同地区的检测强度、实验室覆盖和报告实践会改变同一病例序列的含义。未来需要加入检测量、阳性率、气候、流动和污水等协变量，但前提是这些变量在预测起点之前真实可用，并且其地方含义已经进入知识图谱。",
            ),
            (
                "讨论第5段：五项局限",
                "This study has five main limitations...",
                "局限包括：巴西案例没有执行预测；唯一新鲜盲测失败；香港比较依赖作者发布的预测输出，且外部优势未保持；知识图谱仍是静态核验快照；尚未评价决策效用、公平性、跨站点迁移、概率校准和前瞻预警提前量。因此不能宣称临床或公共卫生有效性。",
            ),
            (
                "讨论第6段：下一步验证",
                "Future prospective evaluations should freeze data and code...",
                "下一步应在分析前冻结数据、代码、预测起点和目标集合，保留简单基线，按病种、地区和数据制度报告失败，并分别消融知识图谱、执行合同、反馈账本和 LLM 智能体。只有经过多站点前瞻评价，才能把“可适应”进一步升级为“能稳定泛化”。",
            ),
        ],
    ),
    (
        "方法：系统如何实现可信自适应",
        [
            (
                "研究设计与证据状态",
                "We designed this system-validation study with nested case studies...",
                "方法上先定义 demonstrated、retrospective、shadow、fresh blind 和 planned 五种证据状态。每个结果只能使用其材料实际支持的最高等级。这个规则避免跨病种实验数量增多后，把不同强度的证据混在一个平均分里。",
            ),
            (
                "智能体与服务架构",
                "Source agents were configured to access approved official surveillance...",
                "数据智能体只访问批准来源，原始数据先归档，再做规范化。可信、预览和预测存储分开。本地 Qwen 只在白名单内提出动作，拟合、交叉验证和推广决定由确定性代码完成，保证跨病种迁移时不会扩大 LLM 的操作权限。",
            ),
            (
                "知识图谱与来源追踪",
                "A document version was keyed by source identity and content hash...",
                "文档版本由来源和哈希标识，证据片段记录页码、偏移、摘录和哈希；人工审核的断言表示病种—模型关系、数据要求、参数证据和限制。每次推荐都保留当时图谱版本，所以知识更新不会改变历史决策。",
            ),
            (
                "数据画像",
                "For each canonical series, the profiler computed...",
                "画像计算观测数量、日期范围、缺失、修订、零值、离散程度、趋势、离群点和变化点，并强制 period_end 不晚于 forecast_origin。多病种自适应的输入不是病名本身，而是这些可计算、带时间边界的数据特征。",
            ),
            (
                "R3C 模型与参数准入",
                "R3C first applied hard semantic gates...",
                "R3C 先按指标语义硬过滤，再根据适用性、数据兼容、证据具体性、来源完整性和实现准备度评分。参数置信度必须超过70。这样模型搜索空间会随病种和数据画像改变，但准入规则保持一致。",
            ),
            (
                "执行合同与治理身份",
                "An ExecutionContract included the data-profile identity and hash...",
                "执行合同绑定数据画像哈希、图谱版本、模型参数、训练和评价窗口、预测起点、风险标记和回滚信息。盲测目标在全局注册，一旦消费就不能通过更换模型名称再次变成新鲜测试。",
            ),
            (
                "香港流感基准数据",
                "We evaluated the Hong Kong influenza benchmark using the official surveillance...",
                "主评价使用4968个共同目标，外部评价使用423个共同目标，缺失预测保持缺失而不补零。这种共同键设计确保 WAPE 差异来自模型，而不是样本集合变化。",
            ),
            (
                "预测期感知编排",
                "DiseaseX evaluated 450 adaptive-orchestration configurations...",
                "独立配置只用开发期选择，采用26周滚动窗口和8周半衰期；路由规则也在开发期冻结。它说明系统能根据预测期选择不同策略，但由于1到8周用了 AWBE，路由结果不能替代独立 DiseaseX 的性能报告。",
            ),
            (
                "受控调优与反馈",
                "For Track C, the tuning dataset comprised 321 training observations...",
                "智利流感把训练、验证和一次性隐藏窗口严格分开。Qwen 只能在白名单中提方案，隐藏标签只消费一次；成熟的模型—基线比较进入反馈账本，但不会修改历史推荐对象。",
            ),
            (
                "指标与统计分析",
                "The primary point-forecast metric was weighted absolute percentage error...",
                "论文以 WAPE 为主要点预测指标，因为它直接衡量总体绝对误差相对真实总量的比例；MAE 和 RMSE 作为次要指标。agreement 只是1减WAPE后的展示量，不是分类准确率。所有比较使用共同目标键。",
            ),
            (
                "配对不确定性与前瞻计划",
                "For the present horizon-specific DiseaseX–AWBE comparison...",
                "新图使用2500次逐行配对 bootstrap 给出探索性区间，并用年度分层 I² 描述时间异质性。因为逐行重采样没有完整保留序列依赖，未来前瞻研究将使用分块 bootstrap，并增加概率评分、区间覆盖和预警提前量。",
            ),
            (
                "软件验证与复现",
                "The final material package passed package-level verification...",
                "材料包通过一致性和秘密扫描，香港流感结果能够从锁定预测面板确定性重放，未来标签检查也通过。论文明确说明这些是对作者发布输出的分析，不是对原始 R/Windows 全栈的完整复跑。",
            ),
            (
                "报告摘要",
                "Further information on the research design...",
                "正式投稿前还需要完成期刊 Reporting Summary。汇报时可以说明研究设计和证据等级已经在正文中固定，但投稿行政材料仍需补齐。",
            ),
        ],
    ),
    (
        "数据、代码、声明与结尾",
        [
            (
                "数据可用性",
                "The Hong Kong influenza benchmark inputs...",
                "香港流感的输入、预测和脚本来自原研究官方仓库；处理后的共同样本、选择锁、指标表和哈希清单应随复现包发布。巴西登革热数据有明确来源键。投稿前仍需补充永久仓库 DOI 和再分发限制。",
            ),
            (
                "代码可用性",
                "The implementation includes source acquisition...",
                "代码覆盖采集、规范化、画像、知识图谱、R3C、执行合同、回测、调优、盲测注册、指标和仪表板。发布前需要固定提交哈希、许可证和最小复现命令，并排除凭据和私有连接器。",
            ),
            (
                "致谢与作者贡献",
                "Acknowledgements; Author contributions",
                "致谢、基金和 CRediT 作者贡献目前仍是占位内容，必须在投稿前由全体作者确认。若期刊要求，还需要披露 LLM 辅助写作或编码的范围。",
            ),
            (
                "利益冲突与伦理",
                "Competing interests; Ethics declarations",
                "当前声明无利益冲突，研究使用公开的聚合监测数据，不涉及受试者招募或干预。最终数据范围确定后，仍需补充机构关于伦理审批或豁免的正式认定。",
            ),
            (
                "结束总结",
                "Overall conclusion",
                "最后总结三点。第一，DiseaseX Nexus 能把不同病种和数据制度转化为带来源、语义和时间边界的数据画像，并据此选择和约束模型。第二，反馈账本允许经验修正知识推荐，同时保留失败和评价身份。第三，现有跨病种结果证明了机制可以迁移，但香港流感外部窗口和新鲜盲测表明预测优势尚未稳定泛化。我们的贡献，是为以后严格的多病种前瞻验证建立可信基础。",
            ),
        ],
    ),
]
