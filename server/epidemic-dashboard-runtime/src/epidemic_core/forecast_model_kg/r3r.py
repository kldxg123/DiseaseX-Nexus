from __future__ import annotations

from typing import Any
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import Engine, text


CONTRACT = "KG_P1_R3R_UX2_EXPLICIT_MODEL_EVIDENCE_v1"
COUNTRY_ALIASES = {"brazil": "BR", "bra": "BR", "br": "BR", "france": "FR", "fra": "FR", "fr": "FR", "united states": "US", "usa": "US", "us": "US", "peru": "PE", "per": "PE", "pe": "PE"}
MODEL_DISPLAY = {
    "model:last_value_baseline": "最后观测值基线",
    "model:seasonal_naive": "季节性 naive 基线",
    "model:regularized_glm": "正则化 GLM-GAM",
    "model:xgboost_tabular": "XGBoost 梯度提升树",
}

MODEL_CANONICAL = {
    "model:last_value_baseline": "last_value_baseline",
    "model:seasonal_naive": "seasonal_naive_baseline",
    "model:regularized_glm": "regularized_glm_gam",
    "model:xgboost_tabular": "xgboost_tabular_gradient_boosted_trees",
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
    r = _rows(engine, sql, params)
    return r[0] if r else None


def _count(engine: Engine | None, sql: str, params: dict[str, Any] | None = None) -> int:
    row = _one(engine, sql, params)
    try:
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _alpha2(country: str | None) -> str | None:
    if not country:
        return None
    v = str(country).strip()
    return COUNTRY_ALIASES.get(v.lower()) or (v.upper() if len(v) in (2, 3) else v)


def _slug(disease_id: str) -> str:
    return {"covid_19": "covid-19", "dengue": "dengue", "influenza": "influenza", "ebola": "ebola-disease", "legionella": "legionella", "hantavirus_puumala": "hantavirus", "nipah": "nipah"}.get(disease_id, disease_id.replace("_", "-"))


def _model_node(engine: Engine | None, key: str) -> dict[str, Any]:
    row = _one(engine, "select node_id::text, canonical_key, display_name from epidemic_kg.kg_nodes where node_type='MODEL_OR_METHOD' and canonical_key=:k limit 1", {"k": key}) or {}
    return {
        "model_id": key,
        "node_id": row.get("node_id"),
        "canonical_name": MODEL_CANONICAL.get(key) or key.replace("model:", ""),
        "display_name_zh": MODEL_DISPLAY.get(key) or row.get("display_name") or key,
        "source_display_name": row.get("display_name"),
    }


def _series(engine: Engine | None, disease_id: str, country: str | None, target: str | None, frequency: str | None) -> dict[str, Any] | None:
    alpha = _alpha2(country)
    rows = _rows(engine, """
        select node_id::text as series_id, display_name, properties
        from epidemic_kg.kg_nodes
        where node_type='DATA_SERIES'
          and coalesce(quarantine,false)=false
          and (cast(:disease as text) is null or properties->>'disease_slug'=cast(:disease as text) or properties->>'disease_name' ilike ('%'||cast(:disease as text)||'%'))
          and (cast(:alpha as text) is null or properties->>'country_alpha2'=cast(:alpha as text) or properties->>'country_alpha3'=cast(:country as text) or properties->>'country_name' ilike ('%'||cast(:country as text)||'%'))
          and (cast(:target as text) is null or properties->>'metric_code'=cast(:target as text))
          and (cast(:freq as text) is null or properties->>'frequency'=cast(:freq as text))
        order by coalesce((properties->>'observation_count')::int,0) desc, display_name
        limit 1
    """, {"disease": _slug(disease_id), "alpha": alpha, "country": country, "target": target, "freq": frequency or "week"})
    return rows[0] if rows else None


def _inventory(engine: Engine | None) -> dict[str, int]:
    unsupported = _count(engine, "select count(*) n from epidemic_kg.kg_claims where not evidence_eligible and not quarantine")
    quarantined = _count(engine, "select count(*) n from epidemic_kg.kg_claims where quarantine")
    return {
        "historical_unique_paper_count": _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='WORK'"),
        "inventory_count": _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='WORK'"),
        "work_node_count": _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='WORK'"),
        "document_version_count": _count(engine, "select count(*) n from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'"),
        "searchable_document_count": _count(engine, "select count(distinct document_node_id) n from epidemic_kg.kg_evidence_spans where evidence_eligible and not quarantine"),
        "eligible_claim_count": _count(engine, "select count(*) n from epidemic_kg.kg_claims where evidence_eligible and not quarantine"),
        "unsupported_claim_count": unsupported,
        "quarantined_claim_count": quarantined,
        "excluded_paper_or_claim_count": unsupported + quarantined,
        "paper1_participation": 0,
    }


def _claims(engine: Engine | None) -> list[dict[str, Any]]:
    return _rows(engine, """
        select c.claim_id::text, c.canonical_key, c.claim_category, c.claim_status, c.review_status,
               c.subject_text, c.predicate_text, c.object_text, c.metric_name,
               coalesce(nullif(c.numeric_value,''),'未提取') numeric_value,
               coalesce(nullif(c.unit,''),'未提取') unit,
               coalesce(nullif(c.comparator_or_baseline,''),'未提取') comparator,
               coalesce(nullif(c.dataset_or_split,''),'未提取') dataset_or_split,
               coalesce(nullif(c.forecast_horizon,''),'未提取') forecast_horizon,
               c.properties,
               w.node_id::text as work_node_id, w.canonical_key as paper_id, w.display_name as paper_title, w.properties as paper_properties,
               d.node_id::text as document_version_id, d.canonical_key as document_id, d.properties as document_properties,
               e.evidence_span_id::text, e.physical_page, e.printed_page_label, e.section_label, e.table_or_figure,
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
    """)


def _data_characteristics(props: dict[str, Any], target: str | None, horizon: int | None) -> list[dict[str, Any]]:
    metric = props.get("metric_code") or target
    obs = int(props.get("observation_count") or 0)
    cov = float(props.get("coverage_ratio") or 0)
    nonzero = int(props.get("nonzero_count") or 0)
    freq = props.get("frequency") or "week"
    target_type = "累计病例" if str(metric or "").endswith("cumulative") else ("新增计数" if str(metric or "").endswith("new") or metric == "cases_new" else "未知目标")
    items = [
        ("target_type", "目标类型", True, target_type, "category", "metric_code", "累计值需要预处理；新增计数可进入计数模型或基线。", "不能把累计值直接当作新增病例。"),
        ("time_frequency", "时间频率", True, freq, "frequency", "properties.frequency", "周频数据适合 weekly horizon 的 rolling-origin 验证。", "不同频率不能复用同一模型结论。"),
        ("history_length", "历史观测数量", True, obs, "weeks", "properties.observation_count", "观测数量决定是否只能 preview/baseline 或可进入正式候选。", "数量不是充分条件，还需覆盖率和无泄漏验证。"),
        ("coverage_ratio", "时间覆盖率", True, cov, "ratio", "properties.coverage_ratio", "覆盖率影响季节性和滞后特征可靠性。", "缺失周不能补零或插值。"),
        ("nonzero_count", "非零观测周", True, nonzero, "weeks", "properties.nonzero_count", "非零周过少时复杂模型不稳。", "非零数量不等于真实发病机制。"),
        ("geographic_grain", "地理粒度", True, props.get("country_alpha2") or "unknown", "country", "properties.country_alpha2", "国家级单序列限制空间模型。", "不能假装拥有州/市级面板。"),
        ("covariates_available", "协变量状态", False, "未接入气象/人口/空间协变量", "status", "no covariate table linked", "缺少协变量时 XGBoost 只能作为数据补齐后的条件首选。", "不能声称当前已具备气候驱动特征。"),
        ("forecast_horizon", "预测周期", True, horizon or 4, "weeks", "request.horizon", "horizon 影响回测切分和模型难度。", "论文 horizon 未报告时不能视作 direct。"),
    ]
    return [{"characteristic_id": cid, "display_name": name, "observed": observed, "value": value, "unit": unit, "source_field_or_query": source, "effect_on_model_choice": effect, "limitation": limitation} for cid, name, observed, value, unit, source, effect, limitation in items]


def _directness(claim: dict[str, Any], disease_id: str, props: dict[str, Any], target: str | None, frequency: str | None, primary_model: str) -> dict[str, Any]:
    blob = " ".join(str(claim.get(k) or "") for k in ("paper_title", "object_text", "subject_text", "dataset_or_split", "paper_id")).lower()
    disease_terms = {"dengue": ["dengue"], "covid_19": ["covid", "sars-cov-2", "coronavirus"]}.get(disease_id, [disease_id.replace("_", " ")])
    disease_match = "MATCH" if any(t in blob for t in disease_terms) else ("COMPATIBLE_GENERAL" if any(t in blob for t in ["infection", "outbreak", "respiratory", "virus", "west nile", "wnv"]) else "MISMATCH")
    geography_match = "MATCH" if str(props.get("country_name") or "").lower() in blob or str(props.get("country_alpha2") or "").lower() in blob else "NOT_REPORTED"
    target_match = "MATCH" if str(target or "").lower() in blob else ("COMPATIBLE_GENERAL" if any(t in blob for t in ["incidence", "case", "outbreak", "death"]) else "NOT_REPORTED")
    frequency_match = "MATCH" if str(frequency or "week").lower() in blob or "weekly" in blob else "NOT_REPORTED"
    horizon_match = "MATCH" if str(claim.get("forecast_horizon") or "").strip() not in {"", "未提取", "NOT_REPORTED_OR_NOT_EXTRACTED"} else "NOT_REPORTED"
    task_match = "COMPATIBLE_GENERAL" if any(t in blob for t in ["forecast", "predict", "prediction"]) else "NOT_REPORTED"
    data_type_match = "COMPATIBLE_GENERAL" if target_match in {"MATCH", "COMPATIBLE_GENERAL"} else "NOT_REPORTED"
    model_match = "MATCH" if ("xgboost" in blob and primary_model == "model:xgboost_tabular") or ("baseline" in blob and "baseline" in primary_model) or ("glm" in blob and primary_model == "model:regularized_glm") or ("gam" in blob and primary_model == "model:regularized_glm") else "NOT_REPORTED"
    validation_match = "COMPATIBLE_GENERAL" if str(claim.get("claim_category")) in {"evaluation_design", "reported_numeric_result", "metric"} else "NOT_REPORTED"
    checks = {"disease_match": disease_match, "geography_match": geography_match, "target_match": target_match, "frequency_match": frequency_match, "horizon_match": horizon_match, "task_match": task_match, "data_type_match": data_type_match, "model_match": model_match, "validation_match": validation_match}
    direct = all(checks[k] == "MATCH" for k in ["disease_match", "geography_match", "target_match", "frequency_match", "horizon_match", "model_match"]) and validation_match in {"MATCH", "COMPATIBLE_GENERAL"}
    if direct:
        label = "DIRECT"
    elif disease_match in {"MATCH", "COMPATIBLE_GENERAL"} and (model_match == "MATCH" or task_match == "COMPATIBLE_GENERAL" or data_type_match == "COMPATIBLE_GENERAL"):
        label = "NEARBY"
    elif disease_match == "MISMATCH" and model_match == "MATCH":
        label = "NEARBY"
    else:
        label = "INSUFFICIENT_METADATA"
    return {"directness": label, **checks}


def _paper_and_claims(engine: Engine | None, disease_id: str, props: dict[str, Any], target: str | None, frequency: str | None, primary_model: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int | str]]:
    claims = []
    papers: dict[str, dict[str, Any]] = {}
    chains = []
    for c in _claims(engine):
        d = _directness(c, disease_id, props, target, frequency, primary_model)
        if d["directness"] not in {"DIRECT", "NEARBY"}:
            continue
        participates = (
            d.get("model_match") == "MATCH"
            and c.get("claim_category") not in {"code_and_data_availability", "availability_only"}
            and c.get("physical_page") is not None
            and c.get("document_version_id")
            and c.get("paper_id")
        )
        if not participates:
            continue
        loc = {"physical_page": c.get("physical_page"), "printed_page": c.get("printed_page_label"), "section": c.get("section_label"), "table_or_figure": c.get("table_or_figure") or "未提取", "short_anchor": c.get("short_anchor")}
        claim = {
            "claim_id": c["claim_id"],
            "paper_id": c["paper_id"],
            "paper_title": c["paper_title"],
            "document_version_id": c["document_version_id"],
            "doi_or_arxiv": (c.get("document_properties") or {}).get("doi") or c.get("document_id"),
            "claim_text_or_readable_paraphrase": (c.get("properties") or {}).get("chinese_interpretation") or f"{c.get('subject_text')} {c.get('predicate_text')} {c.get('object_text')}",
            "claim_relation": "MENTIONS_MODEL" if c.get("claim_category") == "model_architecture_and_inputs" else ("LIMITS_MODEL" if c.get("claim_category") == "limitation_or_transfer_boundary" else "CONTEXT_OR_EVALUATION"),
            "claim_category": c.get("claim_category"),
            "evidence_role": c.get("claim_category"),
            "locator": loc,
            "eligibility": "eligible_located_non_quarantined",
            "directness": d["directness"],
            "directness_checks": {k: v for k, v in d.items() if k != "directness"},
            "similarities": [k for k, v in d.items() if k.endswith("_match") and v in {"MATCH", "COMPATIBLE_GENERAL"}],
            "differences": [k for k, v in d.items() if k.endswith("_match") and v in {"MISMATCH", "NOT_REPORTED"}],
            "transfer_warning": "邻近证据只能支持方法选择的合理性，不能证明当前国家/病种/指标上的最优性。" if d["directness"] == "NEARBY" else "仍需本项目 rolling-origin 回测验证。",
            "effect_on_recommendation": "supports_context" if d["directness"] == "NEARBY" else "direct_support",
        }
        claims.append(claim)
        p = papers.setdefault(c["paper_id"], {"paper_id": c["paper_id"], "paper_title": c["paper_title"], "document_version_ids": set(), "doi_or_arxiv": claim["doi_or_arxiv"], "directness": d["directness"], "claim_ids": [], "recommendation_participation": True})
        p["document_version_ids"].add(c["document_version_id"])
        p["claim_ids"].append(c["claim_id"])
        if p["directness"] != "DIRECT" and d["directness"] == "DIRECT":
            p["directness"] = "DIRECT"
        relation = "supports" if c.get("claim_category") in {"reported_numeric_result", "evaluation_design", "metric"} and d["directness"] == "DIRECT" else ("nearby_context" if d["directness"] == "NEARBY" else "mentions")
        chains.append({
            "chain_id": str(uuid5(NAMESPACE_URL, f"ux2:{primary_model}:{claim['claim_id']}")),
            "model_id": primary_model,
            "data_characteristic_id": "target_type",
            "paper_id": c["paper_id"],
            "paper_title": c["paper_title"],
            "document_version_id": c["document_version_id"],
            "doi_or_arxiv": claim["doi_or_arxiv"],
            "claim_id": claim["claim_id"],
            "claim_text_or_readable_paraphrase": claim["claim_text_or_readable_paraphrase"],
            "claim_relation": relation,
            "evidence_role": claim["evidence_role"],
            "locator": loc,
            "directness": d["directness"],
            "eligibility": claim["eligibility"],
            "similarities": claim["similarities"],
            "differences": claim["differences"],
            "transfer_warning": claim["transfer_warning"],
            "effect_on_recommendation": claim["effect_on_recommendation"],
        })
    paper_list = []
    for p in papers.values():
        p["document_version_ids"] = sorted(p["document_version_ids"])
        p["claim_count"] = len(set(p["claim_ids"]))
        p["claim_ids"] = sorted(set(p["claim_ids"]))
        paper_list.append(p)
    direct = sum(1 for p in paper_list if p["directness"] == "DIRECT")
    nearby = sum(1 for p in paper_list if p["directness"] == "NEARBY")
    status = "DIRECT_PAPER_EVIDENCE" if direct else ("NEARBY_PAPER_EVIDENCE_PLUS_DATA_FIT" if nearby else "NO_ELIGIBLE_SUPPORT")
    return paper_list[:10], claims[:16], chains[:16], {"direct_eligible_paper_count": direct, "nearby_eligible_paper_count": nearby, "recommendation_eligible_claim_count": len(claims), "recommendation_eligible_paper_count": len(paper_list), "paper_support_status": status}


def _decision(engine: Engine | None, disease_id: str, props: dict[str, Any], target: str | None, horizon: int | None, frequency: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    metric = props.get("metric_code") or target
    obs = int(props.get("observation_count") or 0)
    cumulative = str(metric or "").endswith("cumulative")
    current = _model_node(engine, "model:last_value_baseline")
    current.update({"scope": "current_safe_baseline", "current_executable": bool(obs > 0), "reason": "只复制最后一个已观测值，不训练、不声称季节性。"})
    if cumulative:
        primary_key = "model:regularized_glm"
        preprocessing = ["audited_cumulative_to_incident_conversion", "negative_revision_handling", "leakage_safe_cutoff"]
        reason = "累计病例不能直接建模；完成可审计差分后，正则化 GLM/GAM 是比树模型更可解释的首个正式候选。"
    elif disease_id == "dengue":
        primary_key = "model:xgboost_tabular"
        preprocessing = ["preserve_missing_weeks", "add_lag_features", "add_missingness_flags", "optional_versioned_climate_population_covariates"]
        reason = "dengue 新增计数具备周频、较长历史和非线性/滞后风险；数据补齐后 XGBoost 可作为单一条件首选，但当前缺少协变量，不能正式执行。"
    else:
        primary_key = "model:seasonal_naive"
        preprocessing = ["preserve_missing_weeks", "rolling_origin_split"]
        reason = "未命中专门规则时，季节性 naive 是可解释的安全候选。"
    primary = _model_node(engine, primary_key)
    primary.update({"rank": 1, "recommendation_scope": "conditional_after_required_gates", "current_executable": False, "selection_rule_id": "KG_P1_R3R_UX2_SINGLE_MODEL_SELECTION", "selection_rule_version": "v1", "one_sentence_reason": reason})
    alternatives = []
    for key in ["model:seasonal_naive", "model:regularized_glm", "model:xgboost_tabular"]:
        if key != primary_key:
            m = _model_node(engine, key)
            m.update({"rank": len(alternatives) + 2, "reason": "作为候选比较对象，必须由 rolling-origin 回测胜出后才能替代首选。"})
            alternatives.append(m)
    not_rec = [{"model_id": "graph_neural_network", "display_name_zh": "图神经网络", "reason": "当前没有多地区面板和真实空间边。"}, {"model_id": "deep_sequence_model", "display_name_zh": "深度序列模型", "reason": "当前历史长度/样本规模不足，容易过拟合。"}]
    return current, primary, alternatives, not_rec, preprocessing, reason


def augment_guidance_response(engine: Engine | None, result: dict[str, Any], *, disease: str, country: str | None, target: str | None, horizon: int | None, frequency: str | None = "week") -> dict[str, Any]:
    disease_id = result.get("resolved_disease", {}).get("id") or str(disease)
    ser = _series(engine, disease_id, country, target, frequency)
    if not ser:
        result.update({
            "r3r_contract_version": CONTRACT,
            "recommendation_decision": {"status": "NO_MATCHING_SERIES", "reason": "请求的国家/病种/目标/频率没有匹配到 DATA_SERIES；不会复用其他 metric_code 的序列。"},
            "series": {"series_id": None, "display_name": None, "found": False},
            "current_executable_model": None,
            "current_execution_status": "NOT_EXECUTABLE_WITH_REASON",
            "conditional_primary_model": None,
            "required_preprocessing": [],
            "ranked_alternatives": [],
            "not_recommended_models": [],
            "data_characteristics": [],
            "model_data_fit": [],
            "evidence_chains": [],
            "paper_retrieval": {"executed": True, "status": "NO_MATCHING_SERIES", **_inventory(engine)},
            "paper_matches": [],
            "claim_matches": [],
            "paper_inventory_coverage": _inventory(engine),
            "validation_requirements": [],
            "warnings": ["NO_MATCHING_SERIES"],
            "versions": {"recommendation_policy": CONTRACT, "paper_index_revision": "epidemic_kg_existing_claims_v1"},
            "paper1_participation": 0,
        })
        return result
    props = dict(ser.get("properties") or {})
    props["series_id"] = ser.get("series_id")
    requested_target = target or props.get("metric_code")
    semantic_mismatch = requested_target and props.get("metric_code") and requested_target != props.get("metric_code")
    current, primary, alternatives, not_rec, preprocessing, reason = _decision(engine, disease_id, props, requested_target, horizon, frequency)
    chars = _data_characteristics(props, requested_target, horizon)
    papers, claims, chains, support = _paper_and_claims(engine, disease_id, props, requested_target, frequency, (primary or {}).get("model_id") or "")
    inventory = _inventory(engine)
    paper_retrieval = {"executed": True, "status": support["paper_support_status"], "query_terms": {"disease_id": disease_id, "country": country, "target": requested_target, "frequency": frequency or "week", "horizon": horizon}, **inventory, **support}
    data_fit = [{"model_id": primary["model_id"], "data_characteristic_id": c["characteristic_id"], "fit": "supports_choice" if c["observed"] else "missing_required_input", "explanation": c["effect_on_model_choice"], "limitation": c["limitation"]} for c in chars[:5]]
    decision = {
        "status": "QUERY_SERIES_SEMANTIC_MISMATCH" if semantic_mismatch else "MODEL_SELECTED_WITH_EXPLICIT_LIMITS",
        "evidence_source": support["paper_support_status"] if chains else "PROJECT_RULE_PLUS_DATA_FIT",
        "one_sentence": reason if not semantic_mismatch else "请求 target 与 DATA_SERIES metric_code 不一致，推荐已停止。",
    }
    result.update({
        "r3r_contract_version": CONTRACT,
        "recommendation_decision": decision,
        "series": {"series_id": ser.get("series_id"), "display_name": ser.get("display_name"), "found": True},
        "data_profile": props,
        "query_series_semantics": {"request_target": requested_target, "selected_series_metric_code": props.get("metric_code"), "data_profile_metric_code": props.get("metric_code"), "target_semantics": "cumulative" if str(props.get("metric_code") or "").endswith("cumulative") else "incident_or_count", "page_display_target": requested_target, "recommendation_rule_target": requested_target, "consistent": not semantic_mismatch},
        "current_executable_model": None if semantic_mismatch else current,
        "current_execution_status": "NOT_EXECUTABLE_WITH_REASON" if semantic_mismatch else ("CURRENT_BASELINE_AVAILABLE" if current and current.get("current_executable") else "NOT_EXECUTABLE_WITH_REASON"),
        "conditional_primary_model": None if semantic_mismatch else primary,
        "required_preprocessing": preprocessing if not semantic_mismatch else [],
        "ranked_alternatives": alternatives if not semantic_mismatch else [],
        "not_recommended_models": not_rec,
        "data_characteristics": chars,
        "model_data_fit": data_fit if not semantic_mismatch else [],
        "evidence_chains": chains if not semantic_mismatch else [],
        "paper_retrieval": paper_retrieval,
        "paper_matches": papers if not semantic_mismatch else [],
        "claim_matches": claims if not semantic_mismatch else [],
        "paper_inventory_coverage": inventory,
        "validation_requirements": ["six recent rolling origins before production", "compare against last_value, seasonal_naive, moving_average", "feature_max_week <= origin", "label_week <= cutoff", "do not fill missing weeks as zero"],
        "warnings": list(dict.fromkeys(list(result.get("warnings", [])) + (["QUERY_SERIES_SEMANTIC_MISMATCH"] if semantic_mismatch else []) + (["NO_ELIGIBLE_SUPPORT_FOR_PRIMARY_MODEL"] if not chains else []))),
        "versions": {"database_revision": (_one(engine, "select version_num from epidemic.alembic_version limit 1") or {}).get("version_num"), "recommendation_policy": CONTRACT, "paper_index_revision": "epidemic_kg_existing_claims_v1", "paper_evidence_revision": "kg_evidence_spans_locator_v1", "graph_content_sha256": result.get("graph_content_sha256")},
        "paper1_participation": 0,
        # Backward-compatible UX1 fields.
        "current_readiness": {"status": "BASELINE_ONLY" if current else "PREVIEW_ONLY", "can_train_now": False, "facts": {"observations": props.get("observation_count"), "coverage": props.get("coverage_ratio"), "nonzero": props.get("nonzero_count")}, "eligible_actions": ["show_preview", "run_baseline_comparison_only"]},
        "current_baselines": [current] if current else [],
        "conditional_full_data_plan": {"preferred_model": primary, "conditional_alternatives": alternatives, "not_preferred_models": not_rec, "assumed_sufficient_data_conditions": (primary or {}).get("minimum_data", {}), "limitations": result.get("warnings", [])},
    })
    return result
