from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text


COUNTRY_ALIASES = {
    "brazil": "BR",
    "brasil": "BR",
    "bra": "BR",
    "br": "BR",
    "france": "FR",
    "fra": "FR",
    "fr": "FR",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "finland": "FI",
    "fin": "FI",
    "fi": "FI",
    "peru": "PE",
    "per": "PE",
    "pe": "PE",
}


def _rows(engine: Engine | None, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
    except Exception:
        return []


def _one(engine: Engine | None, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = _rows(engine, sql, params)
    return rows[0] if rows else None


def _count(engine: Engine | None, sql: str, params: dict[str, Any] | None = None) -> int:
    row = _one(engine, sql, params)
    try:
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _alpha2(engine: Engine | None, country: str | None) -> str | None:
    if not country:
        return None
    value = str(country).strip()
    if not value:
        return None
    mapped = COUNTRY_ALIASES.get(value.lower())
    if mapped:
        return mapped
    if len(value) == 2:
        return value.upper()
    if len(value) == 3:
        row = _one(engine, "select alpha2 from epidemic.countries where upper(alpha3)=upper(:v) limit 1", {"v": value})
        return (row or {}).get("alpha2") or value.upper()
    row = _one(engine, "select alpha2 from epidemic.countries where name ilike :v limit 1", {"v": value})
    return (row or {}).get("alpha2")


def _disease_slug(disease_id: str) -> str:
    return {
        "covid_19": "covid-19",
        "dengue": "dengue",
        "influenza": "influenza",
        "ebola": "ebola-disease",
        "legionella": "legionella",
        "hantavirus_puumala": "hantavirus",
        "nipah": "nipah",
    }.get(disease_id, disease_id.replace("_", "-"))


def _series(engine: Engine | None, disease_id: str, country: str | None, target: str | None, frequency: str | None) -> dict[str, Any] | None:
    alpha2 = _alpha2(engine, country)
    rows = _rows(
        engine,
        """
        select node_id::text as series_id, display_name, properties
        from epidemic_kg.kg_nodes
        where node_type='DATA_SERIES'
          and coalesce(quarantine,false)=false
          and (cast(:disease_slug as text) is null or properties->>'disease_slug'=cast(:disease_slug as text) or properties->>'disease_name' ilike ('%'||cast(:disease_slug as text)||'%'))
          and (cast(:alpha2 as text) is null or properties->>'country_alpha2'=cast(:alpha2 as text) or properties->>'country_alpha3'=cast(:country as text) or properties->>'country_name' ilike ('%'||cast(:country as text)||'%'))
          and (cast(:target as text) is null or properties->>'metric_code'=cast(:target as text))
          and (cast(:frequency as text) is null or properties->>'frequency'=cast(:frequency as text))
        order by coalesce((properties->>'observation_count')::int,0) desc, display_name
        limit 1
        """,
        {
            "disease_slug": _disease_slug(disease_id),
            "alpha2": alpha2,
            "country": country,
            "target": target,
            "frequency": frequency or "week",
        },
    )
    return rows[0] if rows else None


def _inventory(engine: Engine | None) -> dict[str, int]:
    works = _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='WORK'")
    searchable = _count(
        engine,
        "select count(distinct document_node_id) n from epidemic_kg.kg_evidence_spans where evidence_eligible and not quarantine",
    )
    unsupported = _count(engine, "select count(*) n from epidemic_kg.kg_claims where not evidence_eligible and not quarantine")
    quarantined = _count(engine, "select count(*) n from epidemic_kg.kg_claims where quarantine")
    return {
        "inventory_count": works,
        "local_pdf_count": _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION' and properties ? 'pdf_sha256'"),
        "searchable_text_count": searchable,
        "metadata_only_count": max(0, works - searchable),
        "candidate_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where not quarantine"),
        "deep_validated_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where evidence_eligible and not quarantine"),
        "recommendation_eligible_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where evidence_eligible and not quarantine and canonical_key not like 'claim:paper1:%'"),
        "unsupported_count": unsupported,
        "quarantined_count": quarantined,
        "excluded_paper_or_claim_count": unsupported + quarantined,
        "paper1_participation": 0,
    }


def _role(category: str | None) -> str:
    return {
        "model_architecture_and_inputs": "模型结构与输入",
        "reported_numeric_result": "报告的数值结果",
        "metric": "评价指标",
        "task_target": "任务和目标",
        "study_scope": "研究范围",
        "evaluation_design": "验证设计",
        "limitation_or_transfer_boundary": "适用边界/迁移风险",
        "baseline": "基线比较",
    }.get(str(category or ""), "数据集或方法背景")


def _claim_matches(engine: Engine | None, disease_id: str, target: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    rows = _rows(
        engine,
        """
        select c.claim_id::text, c.canonical_key, c.claim_category, c.claim_status,
               c.subject_text, c.predicate_text, c.object_text, c.metric_name,
               coalesce(nullif(c.numeric_value,''),'未提取') numeric_value,
               coalesce(nullif(c.unit,''),'未提取') unit,
               coalesce(nullif(c.comparator_or_baseline,''),'未提取') comparator,
               coalesce(nullif(c.dataset_or_split,''),'未提取') dataset_or_split,
               coalesce(nullif(c.forecast_horizon,''),'未提取') forecast_horizon,
               c.properties,
               w.display_name as paper_title, w.canonical_key as paper_id, w.properties as paper_properties,
               d.canonical_key as document_id, d.properties as document_properties,
               e.physical_page, e.printed_page_label, e.section_label, e.table_or_figure,
               array_to_string((regexp_split_to_array(coalesce(e.raw_anchor,''),'\\s+'))[1:24],' ') as short_anchor,
               e.anchor_start, e.anchor_end, e.anchor_sha256
        from epidemic_kg.kg_claims c
        join epidemic_kg.kg_nodes w on w.node_id=c.work_node_id
        join epidemic_kg.kg_nodes d on d.node_id=c.document_node_id
        join epidemic_kg.kg_claim_evidence ce on ce.claim_id=c.claim_id
        join epidemic_kg.kg_evidence_spans e on e.evidence_span_id=ce.evidence_span_id and e.evidence_eligible and not e.quarantine
        where c.evidence_eligible and not c.quarantine and c.canonical_key not like 'claim:paper1:%'
        order by c.canonical_key
        limit 120
        """,
    )
    terms = {
        "dengue": ["dengue"],
        "covid_19": ["covid", "sars-cov-2", "coronavirus"],
        "influenza": ["influenza", "flu"],
        "legionella": ["legionella", "legionellosis"],
        "ebola": ["ebola"],
        "nipah": ["nipah"],
        "hantavirus_puumala": ["hantavirus", "puumala"],
    }.get(disease_id, [disease_id.replace("_", " ")])

    direct: list[dict[str, Any]] = []
    nearby: list[dict[str, Any]] = []
    for row in rows:
        blob = " ".join(str(row.get(k) or "") for k in ("paper_title", "object_text", "subject_text", "dataset_or_split", "paper_id")).lower()
        is_direct = any(term in blob for term in terms)
        item = {
            "claim_id": row["claim_id"],
            "paper_id": row["paper_id"],
            "paper_title": row["paper_title"],
            "version": (row.get("document_properties") or {}).get("doi") or row.get("document_id"),
            "disease": disease_id,
            "geography": "直接候选上下文" if is_direct else "邻近/一般证据",
            "target": target or "cases_new",
            "frequency": "未提取",
            "forecast_horizon": row.get("forecast_horizon"),
            "model": row.get("object_text") if row.get("claim_category") == "model_architecture_and_inputs" else "非模型结构 Claim",
            "result": row.get("object_text") if row.get("claim_category") in {"reported_numeric_result", "metric"} else "非数值结果 Claim",
            "comparator": row.get("comparator"),
            "evidence_role": _role(row.get("claim_category")),
            "locator": {
                "physical_page": row.get("physical_page"),
                "printed_page": row.get("printed_page_label"),
                "section": row.get("section_label"),
                "table_or_figure": row.get("table_or_figure") or "未提取",
                "short_anchor": row.get("short_anchor"),
                "anchor_start": row.get("anchor_start"),
                "anchor_end": row.get("anchor_end"),
                "anchor_sha256": row.get("anchor_sha256"),
            },
            "claim_text_or_paraphrase": (row.get("properties") or {}).get("chinese_interpretation")
            or f"{row.get('subject_text')} {row.get('predicate_text')} {row.get('object_text')}",
            "claim_category": row.get("claim_category"),
            "claim_status": row.get("claim_status"),
            "evidence_eligibility": "合格且已定位",
            "transfer_warning": "直接候选仍需核对任务、国家、指标和验证设计" if is_direct else "邻近证据只能提供方法参考，不能当作当前序列的直接复现结论",
            "eligibility": "direct_candidate" if is_direct else "nearby_only",
        }
        (direct if is_direct else nearby).append(item)

    direct_papers = {x["paper_id"] for x in direct}
    nearby_papers = {x["paper_id"] for x in nearby}
    counts = {
        "direct_count": len(direct_papers),
        "nearby_count": len(nearby_papers),
        "direct_claim_count": len(direct),
        "nearby_claim_count": len(nearby),
        "eligible_claim_count": len(rows),
        "returned_count": len((direct or nearby)[:8]),
    }
    return direct[:8], nearby[:8], counts


def _country_context(props: dict[str, Any], disease_id: str) -> dict[str, Any]:
    if props.get("country_alpha2") == "BR":
        return {
            "country": "Brazil",
            "iso2": "BR",
            "iso3": props.get("country_alpha3"),
            "geography_context": "国家级 dengue 序列；巴西国内气候、人口密度和媒介生态存在明显空间异质性。",
            "climate_population_context": "当前不调用外部接口；如果使用气候驱动模型，需要有版本的温度、降雨、湿度、人口或地区面板协变量。",
            "limitation": "没有市/州级协变量时，不能把气候驱动模型当作当前可执行模型。",
        }
    if props.get("country_alpha2") == "FR":
        return {
            "country": "France",
            "iso2": "FR",
            "iso3": props.get("country_alpha3"),
            "geography_context": "国家级 COVID 累计序列；需区分范围、报告制度变化和修订。",
            "climate_population_context": "COVID 推荐重点不是气候画像，而是累计口径转换、政策/检测变化和泄漏安全。",
            "limitation": "cases_cumulative 不能未经审计差分直接建模。",
        }
    return {
        "country": props.get("country_name"),
        "iso2": props.get("country_alpha2"),
        "iso3": props.get("country_alpha3"),
        "limitation": "当前没有专门国家背景；仅使用数据画像和通用项目规则。",
    }


def _plan(props: dict[str, Any], disease_id: str, target: str | None, horizon: int | None, frequency: str | None) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    obs = int(props.get("observation_count") or 0)
    coverage = float(props.get("coverage_ratio") or 0)
    nonzero = int(props.get("nonzero_count") or 0)
    cumulative = str(props.get("metric_code") or target or "").endswith("cumulative")
    formal_ready = obs >= 104 and coverage >= 0.85 and nonzero >= 20 and not cumulative
    baseline_ready = obs >= 52 and not cumulative
    status = "FORMAL_READY_CANDIDATE" if formal_ready else ("BASELINE_ONLY" if baseline_ready else "PREVIEW_ONLY")
    if disease_id == "dengue":
        preferred = {
            "name": "gradient_boosted_trees_xgboost_after_data_completion",
            "display_name": "补齐数据后的梯度提升树 / XGBoost",
            "current_executable": False,
            "capability": "处理季节性、非线性、报告滞后、缺失标记和气象/人口协变量。",
            "minimum_data": {"history_weeks": 104, "coverage": ">=0.85", "nonzero_weeks": ">=20", "covariates": ["temperature", "rainfall", "humidity", "population_or_region_panel"]},
            "current_gap": {"history_weeks_gap": max(0, 104 - obs), "coverage_gap": max(0, round(0.85 - coverage, 4)), "nonzero_weeks_gap": max(0, 20 - nonzero), "covariates_gap": ["temperature", "rainfall", "humidity", "spatial_panel"]},
            "reason": "dengue 周度新增通常具有季节性、媒介生态滞后和非线性；当前可先做 baseline/预览，等历史长度、覆盖率和协变量补齐后，树模型适合作为首选候选。",
            "evidence": "项目规则 + 邻近 vector-borne / eco-climatic 论文证据；不是 BR/dengue 的直接论文复现结论。",
        }
        alternatives = [{"name": "regularized_negative_binomial_or_gam", "display_name": "正则化负二项回归 / GAM", "current_executable": False, "capability": "可解释季节性、趋势和过度离散", "minimum_data": {"history_weeks": "52-104", "coverage": "尽量连续"}, "limitation": "对复杂非线性和空间异质性较弱。"}]
        not_pref = [
            {"name": "graph_neural_network", "display_name": "图神经网络", "reason": "需要多地区面板和真实空间边；当前国家级单序列不足。"},
            {"name": "deep_sequence_model", "display_name": "深度序列模型", "reason": "历史长度和协变量不足时容易过拟合，不能作为首选。"},
        ]
        warnings = ["NO_DIRECT_MATCH_NEARBY_AVAILABLE: 邻近论文证据不能当作 BR/dengue 直接验证。"]
        return status, preferred, alternatives, not_pref, warnings
    if disease_id == "covid_19" or cumulative:
        preferred = {
            "name": "audited_cumulative_to_incident_then_baseline_glm_gam_or_boosting",
            "display_name": "先审计累计转新增，再比较 baseline / GLM-GAM / 树模型",
            "current_executable": False,
            "capability": "先处理累计病例的修订、负差分和泄漏安全，再对周新增做模型比较。",
            "minimum_data": {"audited_conversion": True, "history_weeks": 104, "recent_coverage": ">=0.85", "leakage_safe_cutoff": True},
            "current_gap": {"target_conversion": "required_before_modeling", "negative_revision_check": "required", "history_weeks_gap": max(0, 104 - obs)},
            "reason": "当前目标是累计病例；直接对累计值建模会混淆长期趋势和修订。必须先生成可审计的 cases_new，再用近期 rolling-origin 比较 baseline、GLM/GAM 和树模型。",
            "evidence": "项目规则 + 本地传染病预测论文证据；当前不声称已有 France COVID 累计序列的生产级直接复现。",
        }
        alternatives = [{"name": "state_space_or_ensemble_after_conversion", "display_name": "转换后的状态空间或集成模型", "current_executable": False, "capability": "处理结构变化和短期趋势", "minimum_data": {"converted_weekly_cases": True}, "limitation": "政策和检测变化会造成断点。"}]
        not_pref = [{"name": "direct_model_on_cases_cumulative", "display_name": "直接对累计病例建模", "reason": "未经审计差分会引入口径和泄漏风险。"}]
        warnings = ["CUMULATIVE_TARGET_REQUIRES_AUDITED_CONVERSION"]
        return "PREVIEW_ONLY", preferred, alternatives, not_pref, warnings
    preferred = {
        "name": "seasonal_naive_then_regularized_baseline",
        "display_name": "季节性基线优先",
        "current_executable": baseline_ready,
        "capability": "先提供可解释、可审计的趋势比较。",
        "minimum_data": {"history_weeks": 52},
        "current_gap": {"history_weeks_gap": max(0, 52 - obs)},
        "reason": "当前未命中专门病种规则；在缺少更强证据前，先从可解释 baseline 开始。",
        "evidence": "项目规则。",
    }
    return status, preferred, [], [{"name": "deep_or_graph_first", "display_name": "深度/图模型作为首选", "reason": "当前证据不足。"}], ["NO_SPECIFIC_DISEASE_RECOMMENDER"]


def _paper_status(inv: dict[str, int], direct: list[dict[str, Any]], nearby: list[dict[str, Any]]) -> str:
    if inv.get("searchable_text_count", 0) <= 0 or inv.get("recommendation_eligible_count", 0) <= 0:
        return "PAPER_INDEX_NOT_READY"
    if direct:
        return "DIRECT_MATCH_AVAILABLE"
    if nearby:
        return "NO_DIRECT_MATCH_NEARBY_AVAILABLE"
    return "NO_ELIGIBLE_PAPER_AFTER_COMPLETED_SEARCH"


def augment_guidance_response(
    engine: Engine | None,
    result: dict[str, Any],
    *,
    disease: str,
    country: str | None,
    target: str | None,
    horizon: int | None,
    frequency: str | None = "week",
) -> dict[str, Any]:
    disease_id = result.get("resolved_disease", {}).get("id") or str(disease)
    ser = _series(engine, disease_id, country, target, frequency)
    props = dict(ser.get("properties") or {}) if ser else {}
    status, preferred, alternatives, not_pref, warnings = _plan(props, disease_id, target, horizon, frequency)
    direct, nearby, claim_counts = _claim_matches(engine, disease_id, target)
    shown = direct if direct else nearby
    inv = _inventory(engine)
    rev = (_one(engine, "select version_num from epidemic.alembic_version limit 1") or {}).get("version_num")
    paper_status = _paper_status(inv, direct, nearby)
    props["series_id"] = ser.get("series_id") if ser else None
    props["frequency"] = props.get("frequency") or frequency or "week"
    result.update(
        {
            "r3r_contract_version": "KG_P1_R3R_UX1_v1",
            "series": {"series_id": ser.get("series_id") if ser else None, "display_name": ser.get("display_name") if ser else None, "found": bool(ser)},
            "data_profile": props,
            "country_region_profile": _country_context(props, disease_id),
            "disease_source_profile": {
                "disease": disease_id,
                "source": props.get("source_name"),
                "target_semantics": "cumulative" if str(props.get("metric_code") or target or "").endswith("cumulative") else "reported_incident_or_count",
                "frequency": props.get("frequency"),
            },
            "current_readiness": {
                "status": status,
                "can_train_now": status == "FORMAL_READY_CANDIDATE",
                "facts": {"observations": props.get("observation_count"), "coverage": props.get("coverage_ratio"), "nonzero": props.get("nonzero_count")},
                "eligible_actions": ["show_preview", "run_baseline_comparison_only"] if status == "BASELINE_ONLY" else ["show_preview"],
                "prohibited_interpretations": ["not_production_validated", "not_public_health_forecast"],
                "reasons": [preferred["reason"]],
            },
            "current_baselines": [
                {"name": "last_observed_value", "display_name": "最后观测值基线", "current_executable": True, "capability": "无需训练的保守基线", "minimum_data": {"at_least_one_observation": True}, "limitation": "不能捕捉季节性或结构变化。"},
                {"name": "seasonal_naive", "display_name": "季节性 naive 基线", "current_executable": bool(int(props.get("observation_count") or 0) >= 104), "capability": "与去年同周比较", "minimum_data": {"history_weeks": 104}, "limitation": "稀疏或累计目标需先转换。"},
            ],
            "conditional_full_data_plan": {
                "assumed_sufficient_data_conditions": preferred["minimum_data"],
                "preferred_model": preferred,
                "conditional_alternatives": alternatives,
                "not_preferred_models": not_pref,
                "model_data_compatibility": [{"model": preferred["name"], "display_name": preferred["display_name"], "compatible_after": preferred["minimum_data"], "current_limitation": preferred.get("current_gap")}],
                "project_proposed_initial_config": {"baseline_first": True, "horizon_weeks": horizon or 4, "frequency": frequency or "week"},
                "project_proposed_search_space": preferred.get("minimum_data", {}),
                "limitations": warnings,
            },
            "paper_retrieval": {
                "executed": True,
                "status": paper_status,
                "query_terms": {"disease_id": disease_id, "country": country, "target": target, "frequency": frequency or "week", "horizon": horizon},
                **inv,
                **claim_counts,
                "exclusion_rules": ["paper1 quarantined", "unsupported claims excluded", "availability-only claims excluded from model suitability"],
            },
            "paper_matches": shown,
            "claim_matches": shown,
            "hyperparameter_evidence": [
                {"source_type": "PAPER_DIRECTLY_REPORTED", "value": "未在当前精确查询的合格论文中提取到可直接套用的调参值", "reason": "论文证据用于方法参考；项目参数必须通过本数据集 rolling-origin 验证。"},
                {"source_type": "PROJECT_PROPOSED_INITIAL_CONFIG", "value": {"baseline_first": True, "horizon_weeks": horizon or 4}},
                {"source_type": "PROJECT_PROPOSED_SEARCH_SPACE", "value": preferred.get("minimum_data")},
            ],
            "data_gap_plan": {
                "status": "AVAILABLE",
                "items": ["补齐至少104周并达到覆盖率门槛", "保留缺失周，不补零、不插值", "记录报告延迟和修订", "如使用协变量，必须接入有来源版本的气象、人口或空间面板"],
            },
            "validation_plan": {
                "status": "AVAILABLE",
                "rolling_origin": ["cutoff-24w", "cutoff-20w", "cutoff-16w", "cutoff-12w", "cutoff-8w", "cutoff-4w"],
                "baselines": ["last_value", "seasonal_naive", "moving_average"],
                "metrics": ["MAE", "WAPE", "horizon-wise error", "interval coverage"],
                "leakage_controls": ["feature_max_week <= origin", "label_week <= cutoff"],
            },
            "audit_rationale": [
                {"fact": "本地论文索引已执行", "source": "epidemic_kg.kg_claims + kg_evidence_spans", "source_version_or_date": str(rev), "rule_id": "KG_P1_R3R_UX1_PAPER_STATE", "rule_version": "v1", "effect_on_recommendation": paper_status, "evidence_role": "DATASET_CONTEXT", "eligibility": "eligible", "limitation": "邻近证据不是直接证明。"},
                {"fact": "paper1 participation = 0", "source": "canonical_key exclusion claim:paper1:%", "source_version_or_date": str(rev), "rule_id": "KG_P1_R3R_PAPER1_EXCLUDED", "rule_version": "v1", "effect_on_recommendation": "excluded", "evidence_role": "LIMITATION", "eligibility": "required", "limitation": "正确 2025 Nature 正式版 PDF 仍未闭合。"},
                {"fact": preferred["reason"], "source": "project rule plus live data profile", "source_version_or_date": str(rev), "rule_id": "KG_P1_R3R_UX1_CONDITIONAL_MODEL", "rule_version": "v1", "effect_on_recommendation": preferred["display_name"], "evidence_role": "PROJECT_RULE", "eligibility": "conditional", "limitation": "必须由 rolling-origin 回测决定生产可用性。"},
            ],
            "warnings": list(dict.fromkeys(list(result.get("warnings", [])) + warnings)),
            "versions": {
                "database_revision": rev,
                "recommendation_policy": "KG_P1_R3R_UX1_v1",
                "paper_index_revision": "epidemic_kg_existing_claims_v1",
                "paper_evidence_revision": "kg_evidence_spans_locator_v1",
                "country_context_revision": "R3R_UX1_local_context_v1",
                "graph_content_sha256": result.get("graph_content_sha256"),
            },
            "paper1_participation": 0,
        }
    )
    return result
