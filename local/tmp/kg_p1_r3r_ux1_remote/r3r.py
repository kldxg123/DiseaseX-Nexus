from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text


def _rows(engine: Engine | None, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if engine is None:
        return []
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def _one(engine: Engine | None, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = _rows(engine, sql, params)
    return rows[0] if rows else None


def _count(engine: Engine | None, sql: str) -> int:
    row = _one(engine, sql) or {"n": 0}
    return int(row.get("n") or 0)


def _disease_slug(disease_id: str) -> str:
    return {
        "covid_19": "covid-19",
        "influenza": "influenza",
        "dengue": "dengue",
        "ebola": "ebola-disease",
        "hantavirus_puumala": "hantavirus",
        "legionella": "legionella",
        "nipah": "nipah",
    }.get(disease_id, disease_id)


def _alpha2(engine: Engine | None, value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip()
    if len(v) == 2:
        return v.upper()
    aliases = {"BRA": "BR", "BRAZIL": "BR", "BRASIL": "BR", "FRA": "FR", "FRANCE": "FR", "PER": "PE", "PERU": "PE", "UNITED STATES": "US", "USA": "US"}
    if v.upper() in aliases:
        return aliases[v.upper()]
    row = _one(
        engine,
        """
        select alpha2 from epidemic.countries
        where upper(alpha2)=upper(:v) or upper(alpha3)=upper(:v) or lower(name)=lower(:v)
        limit 1
        """,
        {"v": v},
    )
    if row:
        return str(row["alpha2"]).upper()
    return v[:2].upper()


def _series(engine: Engine | None, disease_id: str, country: str | None, target: str | None, frequency: str | None) -> dict[str, Any] | None:
    rows = _rows(
        engine,
        """
        select node_id::text as series_id, display_name, properties
        from epidemic_kg.kg_nodes
        where node_type='DATA_SERIES' and quarantine=false
          and properties->>'disease_slug'=:disease_slug
          and (cast(:alpha2 as text) is null or properties->>'country_alpha2'=cast(:alpha2 as text) or properties->>'country_alpha3'=cast(:country as text))
          and (cast(:target as text) is null or properties->>'metric_code'=cast(:target as text))
          and (cast(:frequency as text) is null or properties->>'frequency'=cast(:frequency as text))
        order by coalesce((properties->>'observation_count')::int,0) desc, display_name
        limit 1
        """,
        {
            "disease_slug": _disease_slug(disease_id),
            "alpha2": _alpha2(engine, country),
            "country": country,
            "target": target,
            "frequency": frequency or "week",
        },
    )
    return rows[0] if rows else None


def _inventory(engine: Engine | None) -> dict[str, int]:
    work = _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='WORK'")
    searchable = _count(
        engine,
        "select count(distinct document_node_id) n from epidemic_kg.kg_evidence_spans where evidence_eligible and not quarantine",
    )
    return {
        "inventory_count": work,
        "local_pdf_count": _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION' and properties ? 'pdf_sha256'"),
        "searchable_text_count": searchable,
        "metadata_only_count": max(0, work - searchable),
        "candidate_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where not quarantine"),
        "deep_validated_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where evidence_eligible and not quarantine"),
        "recommendation_eligible_count": _count(
            engine,
            "select count(*) n from epidemic_kg.kg_claims where evidence_eligible and not quarantine and canonical_key not like 'claim:paper1:%'",
        ),
        "unsupported_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where not evidence_eligible and not quarantine"),
        "quarantined_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where quarantine"),
        "paper1_participation": 0,
    }


def _role(category: str | None) -> str:
    return {
        "model_architecture_and_inputs": "MODEL_CONFIGURATION",
        "reported_numeric_result": "MODEL_PERFORMANCE",
        "metric": "MODEL_PERFORMANCE",
        "task_target": "TASK_AND_TARGET_MATCH",
        "study_scope": "DATASET_CONTEXT",
        "evaluation_design": "MODEL_PERFORMANCE",
        "limitation_or_transfer_boundary": "LIMITATION",
        "baseline": "MODEL_PERFORMANCE",
        "code_and_data_availability": "REPRODUCIBILITY_OR_AVAILABILITY",
    }.get(str(category or ""), "DATASET_CONTEXT")


def _claim_matches(engine: Engine | None, disease_id: str, target: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    rows = _rows(
        engine,
        """
        select c.claim_id::text, c.canonical_key, c.claim_category, c.claim_status,
               c.subject_text, c.predicate_text, c.object_text, c.metric_name,
               coalesce(nullif(c.numeric_value,''),'NOT_REPORTED_OR_NOT_EXTRACTED') numeric_value,
               coalesce(nullif(c.unit,''),'NOT_REPORTED_OR_NOT_EXTRACTED') unit,
               coalesce(nullif(c.comparator_or_baseline,''),'NOT_REPORTED_OR_NOT_EXTRACTED') comparator,
               coalesce(nullif(c.dataset_or_split,''),'NOT_REPORTED_OR_NOT_EXTRACTED') dataset_or_split,
               coalesce(nullif(c.forecast_horizon,''),'NOT_REPORTED_OR_NOT_EXTRACTED') forecast_horizon,
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
        limit 80
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
            "geography": "nearby/general" if not is_direct else "direct context candidate",
            "target": target or "cases_new",
            "frequency": "NOT_REPORTED_OR_NOT_EXTRACTED",
            "forecast_horizon": row.get("forecast_horizon"),
            "model": row.get("object_text") if row.get("claim_category") == "model_architecture_and_inputs" else "NOT_MODEL_CLAIM",
            "result": row.get("object_text") if row.get("claim_category") in {"reported_numeric_result", "metric"} else "NOT_NUMERIC_RESULT_CLAIM",
            "comparator": row.get("comparator"),
            "evidence_role": _role(row.get("claim_category")),
            "locator": {
                "physical_page": row.get("physical_page"),
                "printed_page": row.get("printed_page_label"),
                "section": row.get("section_label"),
                "table_or_figure": row.get("table_or_figure") or "NOT_REPORTED_OR_NOT_EXTRACTED",
                "short_anchor": row.get("short_anchor"),
                "anchor_start": row.get("anchor_start"),
                "anchor_end": row.get("anchor_end"),
                "anchor_sha256": row.get("anchor_sha256"),
            },
            "claim_text_or_paraphrase": (row.get("properties") or {}).get("chinese_interpretation")
            or f"{row.get('subject_text')} {row.get('predicate_text')} {row.get('object_text')}",
            "claim_category": row.get("claim_category"),
            "claim_status": row.get("claim_status"),
            "evidence_eligibility": "ELIGIBLE_LOCATED",
            "transfer_warning": "DIRECT_MATCH_REQUIRES_TASK_CONTEXT_CHECK" if is_direct else "NEARBY_EVIDENCE_ONLY_NOT_DIRECTLY_VALIDATED_FOR_CURRENT_QUERY",
            "eligibility": "direct_candidate" if is_direct else "nearby_only",
        }
        (direct if is_direct else nearby).append(item)
    return direct[:8], nearby[:8], {"candidate_count": len(rows), "direct_count": len(direct), "nearby_count": len(nearby), "returned_count": len((direct or nearby)[:8])}


def _country_context(props: dict[str, Any], disease_id: str) -> dict[str, Any]:
    if props.get("country_alpha2") == "BR":
        return {
            "country": "Brazil",
            "iso2": "BR",
            "iso3": props.get("country_alpha3"),
            "geography_context": "国家级序列；Brazil 国内气候、人口密度和蚊媒生态存在强空间异质性。",
            "climate_population_context": "R3R 不调用外部接口；当前只能声明需要温度、降雨、湿度、人口/地区面板等协变量，不能猜测具体气候值。",
            "limitation": "无市/州级协变量时，不能把气候驱动模型作为当前可执行模型。",
        }
    if props.get("country_alpha2") == "FR":
        return {
            "country": "France",
            "iso2": "FR",
            "iso3": props.get("country_alpha3"),
            "geography_context": "国家级 COVID 累计序列；需避免把本土与海外范围、报告制度变化和修订混为稳定周新增。",
            "climate_population_context": "COVID 推荐重点不是气候画像，而是累计口径转换、政策/检测变化和泄漏安全。",
            "limitation": "cases_cumulative 不能未经审计差分直接建模。",
        }
    return {"country": props.get("country_name"), "iso2": props.get("country_alpha2"), "iso3": props.get("country_alpha3"), "limitation": "COUNTRY_CONTEXT_MISSING"}


def _plan(props: dict[str, Any], disease_id: str, target: str | None) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    obs = int(props.get("observation_count") or 0)
    cumulative = str(props.get("metric_code") or target or "").endswith("cumulative")
    status = "BASELINE_ONLY" if obs >= 52 and not cumulative else "PREVIEW_ONLY"
    if disease_id == "dengue":
        return status, {
            "name": "gradient_boosted_trees_xgboost_after_data_completion",
            "display_name": "补齐数据后的梯度提升树 / XGBoost",
            "capability": "处理非线性、滞后、缺失标记和气象/人口协变量。",
            "minimum_data": {"history_weeks": 104, "coverage": ">=0.85", "nonzero_weeks": ">=20", "covariates": ["temperature", "rainfall", "humidity", "population_or_region_panel"]},
            "current_gap": {"history_weeks_gap": max(0, 104 - obs), "covariates_gap": ["temperature", "rainfall", "humidity", "spatial panel"]},
            "reason": "登革热病例新增具有季节性、媒介生态滞后和非线性；当前可先做 baseline，补齐协变量与历史后再把树模型作为首选候选。",
            "evidence": "PROJECT_RULE + NEARBY vector-borne eco-climatic paper evidence; no direct BR dengue proof.",
        }, [{"name": "regularized_negative_binomial_or_gam", "capability": "可解释季节性/趋势", "minimum_data": "52-104 weeks", "limitation": "非线性和空间异质性较弱"}], [{"name": "graph_neural_network", "limitation": "需要多地区面板与真实空间边；当前国家级单序列不足"}, {"name": "deep_sequence_model", "limitation": "97周不足以支撑深度模型稳健验证"}], ["NO_DIRECT_PAPER_MATCH: 邻近病种/方法证据不构成 BR dengue 直接验证。"]
    if disease_id == "covid_19" or cumulative:
        return "PREVIEW_ONLY", {
            "name": "audited_cumulative_to_incident_then_baseline_glm_gam_or_boosting",
            "display_name": "先审计累计转新增，再比较 baseline/GLM-GAM/树模型",
            "capability": "处理累计病例修订、负差分和泄漏安全后再预测周新增。",
            "minimum_data": {"audited_conversion": True, "history_weeks": 104, "recent_coverage": ">=0.85", "leakage_safe_cutoff": True},
            "current_gap": {"target_conversion": "required_before_modeling", "negative_revision_check": "required", "history_weeks_gap": max(0, 104 - obs)},
            "reason": "France COVID 当前目标为累计病例；直接对累计值建模会混淆趋势和修订，必须先生成可审计的 cases_new。",
            "evidence": "PROJECT_RULE + nearby multi-region infectious disease evidence; direct France COVID paper proof not claimed.",
        }, [{"name": "state_space_or_ensemble_after_conversion", "capability": "处理结构变化和短期趋势", "minimum_data": "converted weekly cases with recent origins", "limitation": "政策/检测变化会造成断点"}], [{"name": "direct_model_on_cases_cumulative", "limitation": "累计值未经差分审计不得直接训练"}], ["CUMULATIVE_TARGET_REQUIRES_AUDITED_CONVERSION", "NO_DIRECT_PAPER_MATCH: 不冒充 direct France COVID paper evidence。"]
    return status, {"name": "seasonal_naive_then_regularized_baseline", "display_name": "季节性基线优先", "capability": "保守趋势比较", "minimum_data": {"history_weeks": 52}, "current_gap": {}, "reason": "未命中专门病种规则。", "evidence": "PROJECT_RULE"}, [], [], ["NO_SPECIFIC_DISEASE_RECOMMENDER"]


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
    status, preferred, alternatives, not_pref, warnings = _plan(props, disease_id, target)
    direct, nearby, claim_counts = _claim_matches(engine, disease_id, target)
    shown = direct if direct else nearby
    inv = _inventory(engine)
    rev = (_one(engine, "select version_num from epidemic.alembic_version limit 1") or {}).get("version_num")
    paper_status = "DIRECT_MATCH_AVAILABLE" if direct else "NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE"
    if inv["searchable_text_count"] == 0:
        paper_status = "BLOCKED_PAPER_INDEX_NOT_READY"
    props["series_id"] = ser.get("series_id") if ser else None
    props["frequency"] = props.get("frequency") or frequency or "week"
    result.update(
        {
            "r3r_contract_version": "KG_P1_R3R_v1",
            "series": {"series_id": ser.get("series_id") if ser else None, "display_name": ser.get("display_name") if ser else None, "found": bool(ser)},
            "data_profile": props,
            "country_region_profile": _country_context(props, disease_id),
            "disease_source_profile": {"disease": disease_id, "source": props.get("source_name"), "target_semantics": "cumulative" if str(props.get("metric_code") or target or "").endswith("cumulative") else "reported_incident_or_count", "frequency": props.get("frequency")},
            "current_readiness": {"status": status, "facts": {"observations": props.get("observation_count"), "coverage": props.get("coverage_ratio"), "nonzero": props.get("nonzero_count")}, "eligible_actions": ["show_preview", "run_baseline_comparison_only"] if status == "BASELINE_ONLY" else ["show_preview"], "prohibited_interpretations": ["not_production_validated", "not_public_health_forecast"], "reasons": [preferred["reason"]]},
            "current_baselines": [{"name": "last_observed_value", "capability": "no-training baseline", "minimum_data": "at least one observation", "limitation": "cannot model seasonality"}, {"name": "seasonal_naive", "capability": "seasonal baseline", "minimum_data": "104 weekly observations preferred", "limitation": "not usable for sparse/cumulative targets without conversion"}],
            "conditional_full_data_plan": {"assumed_sufficient_data_conditions": preferred["minimum_data"], "preferred_model": preferred, "conditional_alternatives": alternatives, "not_preferred_models": not_pref, "model_data_compatibility": [{"model": preferred["name"], "compatible_after": preferred["minimum_data"], "current_limitation": preferred.get("current_gap")}], "project_proposed_initial_config": {"baseline_first": True, "horizon_weeks": horizon or 4, "frequency": frequency or "week"}, "project_proposed_search_space": preferred.get("minimum_data", {}), "limitations": warnings},
            "paper_retrieval": {"executed": True, "status": paper_status, "query_terms": {"disease_id": disease_id, "country": country, "target": target, "frequency": frequency or "week", "horizon": horizon}, **inv, **claim_counts, "exclusion_rules": ["paper1 quarantined", "unsupported claims excluded", "availability-only does not increase suitability"]},
            "paper_matches": shown,
            "claim_matches": shown,
            "hyperparameter_evidence": [{"source_type": "PAPER_DIRECTLY_REPORTED", "value": "NOT_REPORTED_OR_NOT_EXTRACTED", "reason": "No displayed eligible paper directly reports tunable hyperparameters for this exact query."}, {"source_type": "PROJECT_PROPOSED_INITIAL_CONFIG", "value": {"baseline_first": True, "horizon_weeks": horizon or 4}}, {"source_type": "PROJECT_PROPOSED_SEARCH_SPACE", "value": preferred.get("minimum_data")}],
            "data_gap_plan": {"status": "AVAILABLE", "items": ["补齐至少104周且覆盖率达到门槛", "保留缺失周而不是补零", "记录报告延迟和修订", "如使用协变量则接入有来源版本的气象/人口/空间面板"]},
            "validation_plan": {"status": "AVAILABLE", "rolling_origin": ["cutoff-24w", "cutoff-20w", "cutoff-16w", "cutoff-12w", "cutoff-8w", "cutoff-4w"], "baselines": ["last_value", "seasonal_naive", "moving_average"], "metrics": ["MAE", "WAPE", "horizon-wise error", "interval coverage"], "leakage_controls": ["feature_max_week <= origin", "label_week <= cutoff"]},
            "audit_rationale": [{"fact": "paper index executed", "source": "epidemic_kg.kg_claims + kg_evidence_spans", "source_version_or_date": str(rev), "rule_id": "KG_P1_R3R_PAPER_INDEX", "rule_version": "v1", "effect_on_recommendation": paper_status, "evidence_role": "DATASET_CONTEXT", "eligibility": "eligible", "limitation": "nearby evidence is not direct proof"}, {"fact": "paper1 participation is zero", "source": "canonical_key exclusion claim:paper1:%", "source_version_or_date": str(rev), "rule_id": "KG_P1_R3R_PAPER1_EXCLUDED", "rule_version": "v1", "effect_on_recommendation": "excluded", "evidence_role": "LIMITATION", "eligibility": "required", "limitation": "correct 2025 Nature PDF still absent"}, {"fact": preferred["reason"], "source": "project rule plus live data profile", "source_version_or_date": str(rev), "rule_id": "KG_P1_R3R_CONDITIONAL_MODEL", "rule_version": "v1", "effect_on_recommendation": preferred["name"], "evidence_role": "PROJECT_RULE", "eligibility": "conditional", "limitation": "must be validated by rolling-origin before production"}],
            "warnings": list(dict.fromkeys(list(result.get("warnings", [])) + warnings + ([] if direct else ["NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE"]))),
            "versions": {"database_revision": rev, "recommendation_policy": "KG_P1_R3R_v1", "paper_index_revision": "epidemic_kg_existing_claims_v1", "paper_evidence_revision": "kg_evidence_spans_locator_v1", "country_context_revision": "R3R_local_context_v1", "graph_content_sha256": result.get("graph_content_sha256")},
            "paper1_participation": 0,
        }
    )
    return result
