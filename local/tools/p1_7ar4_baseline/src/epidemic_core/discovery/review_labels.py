from __future__ import annotations

LABEL_VERSION = "review-ui-labels-v2"

LABELS: dict[str, tuple[str, str, str, str]] = {
    "mapped": ("用于入库", "该原始列已映射到标准字段或指标。", "决定正式数据的含义。", "核对字段语义、单位与转换。"),
    "raw-only": (
        "仅保留原始证据",
        "该列保留在隔离证据中，不进入正式预测数据。",
        "避免把辅助字段误当病例。",
        "无需映射，必要时补充说明。",
    ),
    "ignored": ("忽略", "该列已明确不使用。", "减少无关或危险字段进入流水线。", "确认忽略不会损失核心指标。"),
    "unsupported": ("暂不支持", "当前标准模型无法表达该列。", "强行映射会改变原始含义。", "先实现对应数据模型。"),
    "canonical metric": (
        "标准指标",
        "平台统一使用的病例、死亡、率等指标。",
        "保证跨来源比较一致。",
        "确认 count/rate/percentage 类型。",
    ),
    "reporting period": (
        "报告期",
        "来源汇总并发布数据的时间区间。",
        "不等同于感染实际发生日。",
        "确认开始、结束与粒度。",
    ),
    "null/0": ("缺失与零", "缺失表示未提供；0 表示明确报告为零。", "两者不可互换。", "逐列核对空值规则。"),
    "count/rate/percentage": (
        "数值类型",
        "计数、率和百分比具有不同分母与单位。",
        "错误混用会产生错误结论。",
        "确认指标类型和单位。",
    ),
    "model-derived": ("模型派生", "由模型估计而非官方直接报告。", "不得冒充官方病例或死亡。", "仅保留原始证据。"),
    "REVIEW_READY": (
        "等待人工复核",
        "技术材料已生成，但尚未获准入库。",
        "机器检查不能替代真人判断。",
        "阅读三张表并处理阻断。",
    ),
    "NEEDS_IMPLEMENTATION": (
        "需要先实现",
        "现有正式模型尚不能安全承载该数据。",
        "批准会造成错误语义。",
        "完成实现后重新生成材料。",
    ),
    "UNSUPPORTED_GEOGRAPHIC_GRAIN": (
        "地理层级不支持",
        "来源粒度与当前正式模型不匹配。",
        "市级数据不能作为全国数据。",
        "实现并核验标准地点映射。",
    ),
    "MAPPING_SOURCE_FIELD_MISSING": (
        "映射字段缺失",
        "映射引用的列在真实文件中不存在。",
        "无法证明值来自哪个来源字段。",
        "确认真实字段含义并创建新 revision。",
    ),
    "CUSTOM_REVIEW_REQUIRED": (
        "许可需要人工核验",
        "当前许可不是已登记的自动可用许可。",
        "目前还不能据此入库。",
        "核验条款、再分发和衍生使用权限。",
    ),
    "PERSON_LEVEL_DATA": (
        "疑似个人级数据",
        "数据可能包含可识别个人记录。",
        "涉及隐私和合规硬阻断。",
        "停止处理并转交合规审查。",
    ),
}

STATE_ZH = {
    "QUEUED": "等待后台领取",
    "DISCOVERING": "正在检索官方目录",
    "CANDIDATES_FOUND": "已找到候选",
    "PROFILING": "正在分析候选",
    "REVIEW_READY": "候选等待人工复核",
    "NO_CANDIDATE": "未找到合格候选",
    "DUPLICATE_ONLY": "仅发现已登记重复来源",
    "POLICY_BLOCKED": "策略阻断",
    "SECURITY_BLOCKED": "安全阻断",
    "LICENSE_BLOCKED": "许可阻断",
    "UNSUPPORTED_FORMAT": "格式暂不支持",
    "INVALID_REQUEST": "请求字段不完整",
    "FAILED_RETRYABLE": "暂时失败，等待重试",
    "FAILED_TERMINAL": "重试耗尽，任务失败",
}

REASON_ZH = {
    "GENERIC_SEARCH_PROVIDER_UNCONFIGURED": (
        "通用搜索 Provider 未配置，已检查允许的目录策略但没有可安全查询的国家目录。"
    ),
    "MISSING_OR_INVALID_REQUEST_FIELDS": "请求缺少必需字段或字段格式无效。",
    "INVALID_REQUEST_FIELDS": "请求字段无法通过严格校验。",
    "WORKER_TASK_EXCEPTION": "单次任务发生异常，Worker 仍在运行并将在到期后重试。",
    "WORKER_TASK_RETRY_EXHAUSTED": "任务连续失败并已达到有限重试次数。",
    "BLOCKED_GENERIC_SEARCH_CONFIGURATION": "系统缺少通用检索 API 配置；检索覆盖不完整，不能据此判断没有数据源。",
    "DISCOVERY_INCOMPLETE": "至少一个必要检索渠道没有成功完成，当前结果不能视为完整检索。",
    "NO_CANDIDATE_COMPLETE": "本次选择范围内的全部必要渠道均已真实执行，但没有合格候选。",
    "CANDIDATES_AWAITING_HUMAN_REVIEW": "候选已进入隔离复核区，尚未批准或写入正式疫情数据。",
}

TRANSFORM_ZH = {
    "identity": "保持原值，不改变语义。",
    "parse_iso_date": "将 ISO 日期转换成标准报告日期。",
    "parse_epiweek": "将流行病学年/周转换成标准周起止日期。",
    "parse_isoweek": "将 ISO 年/周转换成标准周起止日期。",
    "parse_month": "将年月转换成标准月起止日期。",
    "country_name_to_iso3": "把国家名称解析为 ISO3 代码。",
    "numeric_count": "解析为计数；保留 0 与负修订。",
    "numeric_rate": "解析为率；必须同时核验分母和单位。",
    "explicit_null_tokens": "把明确的缺失标记转换为缺失，不转换 0。",
}

FIELD_ZH = {
    "data_iniSE": "报告周开始日期",
    "SE": "流行病学年周",
    "casos": "报告病例数",
    "obitos": "报告死亡数",
    "Localidade_id": "来源中的地区标识（具体语义待确认）",
    "cidade": "地区名称",
    "Rt": "模型估计的有效再生数",
    "casos_est": "模型估计病例数",
    "casos_est_min": "模型估计病例数下界",
    "casos_est_max": "模型估计病例数上界",
    "p_rt1": "Rt 大于 1 的模型概率",
    "nivel": "模型风险等级",
}

MODEL_FIELDS = {"Rt", "casos_est", "casos_est_min", "casos_est_max", "p_rt1", "nivel"}
