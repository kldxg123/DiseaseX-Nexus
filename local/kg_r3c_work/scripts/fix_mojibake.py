#!/usr/bin/env python3
"""Repair pre-existing '?'-mojibake in the legacy forecast-model-KG page template/JS.
Display strings only; no logic, data, or route changes. Reversible via git.
"""
import io, sys

PAIRS_PY = [
    ("<title>???????? / Forecast Model Knowledge Graph</title>",
     "<title>预测模型知识图谱 / Forecast Model Knowledge Graph</title>"),
    ("<h1>????????????????????</h1>",
     "<h1>预测模型知识图谱（执行前证据评分）</h1>"),
    ("<p>?????<strong>PRE_EXECUTION_EVIDENCE_SCORE</strong> / ???????????????????????????????????????????????????</p>",
     "<p>本页分数类型为 <strong>PRE_EXECUTION_EVIDENCE_SCORE</strong> / 仅为执行前证据评分，不构成任何预测精度或回测性能承诺。</p>"),
    ("<strong>?????????????????</strong>",
     "<strong>执行前证据评分免责声明</strong>"),
    ("??????????????actually_executed=false?fit_called=false?predict_called=false?local_backtest_status=DEFERRED_TO_R3B?forecast_run_id=null?",
     "本页所有候选均未实际执行：actually_executed=false，fit_called=false，predict_called=false；local_backtest_status=DEFERRED_TO_R3B；forecast_run_id=null。"),
    ('<label>?? <select id="kgCountry"></select></label>', '<label>国家 <select id="kgCountry"></select></label>'),
    ('<label>?? <select id="kgDisease"></select></label>', '<label>病种 <select id="kgDisease"></select></label>'),
    ('<label>?? <select id="kgMetric"></select></label>', '<label>指标 <select id="kgMetric"></select></label>'),
    ('<label>?? <select id="kgCadence"></select></label>', '<label>频率 <select id="kgCadence"></select></label>'),
    ('<button id="kgRun" type="button">?????</button>', '<button id="kgRun" type="button">运行评估</button>'),
    ('<article class="kg-wide"><h2>?????????????????</h2><div id="kgModels"></div></article>',
     '<article class="kg-wide"><h2>模型配置候选清单</h2><div id="kgModels"></div></article>'),
    ('<article><h2>A?F ????</h2><div id="kgWaterfall"></div></article>',
     '<article><h2>A–F 维度瀑布</h2><div id="kgWaterfall"></div></article>'),
    ('<article><h2>??????</h2><div id="kgMatrix"></div></article>',
     '<article><h2>候选上下文矩阵</h2><div id="kgMatrix"></div></article>'),
    ('<article><h2>?????? locator</h2><div id="kgEvidence"></div></article>',
     '<article><h2>证据点 locator</h2><div id="kgEvidence"></div></article>'),
    ('<article><h2>???????????</h2><div id="kgParams"></div></article>',
     '<article><h2>参数包与来源层级</h2><div id="kgParams"></div></article>'),
    ('<article><h2>???? / ?? fallback</h2><svg id="kgGraph"',
     '<article><h2>候选图 / 表格 fallback</h2><svg id="kgGraph"'),
    ('<article><h2>???????? baseline ????</h2><div id="kgExcluded"></div></article>',
     '<article><h2>被排除的 baseline 与原因</h2><div id="kgExcluded"></div></article>'),
    ('<article><h2>????</h2><div id="kgDeferred"></div><button disabled>?? R3B ????????????????????</button></article>',
     '<article><h2>延期执行</h2><div id="kgDeferred"></div><button disabled>待 R3B 执行合同授权后方可运行</button></article>'),
    ('"disclaimer":"?????????????????????????????????",',
     '"disclaimer":"本分数为执行前证据评分，非预测精度或本地回测性能承诺。",'),
]

PAIRS_JS = [
    ("<b>PRE_EXECUTION_EVIDENCE_SCORE</b> ? ${d.items.length} ?? baseline ?? ? baseline_in_preexecution_recommendation_count=",
     "<b>PRE_EXECUTION_EVIDENCE_SCORE</b>：候选 ${d.items.length} 个；进入正式推荐的 baseline 数：baseline_in_preexecution_recommendation_count="),
    ("class=\"kg-detail\">??????</button>", "class=\"kg-detail\">查看明细</button>"),
    ("table(['??','??????','????????','?????','????','??','????','forecast_run_id'], rows)",
     "table(['候选','模型名称','执行前证据分','参数可信分','证据覆盖','解释','回测状态','forecast_run_id'], rows)"),
    ("table(['?','??','??','??','??'], comps", "table(['组','规则','得分','满分','贡献'], comps"),
    ("table(['??','????'], [['??',c.country_alpha3],['??',c.disease_slug],['??',c.metric_code],['??',c.cadence],",
     "table(['字段','值'], [['国家',c.country_alpha3],['病种',c.disease_slug],['指标',c.metric_code],['频率',c.cadence],"),
    ("table(['Evidence point','??','??','??','???','locator'], ev.map(x=>[esc(x.point_id),esc(x.model_key),esc(x.disease_slug),esc(x.point_type),",
     "table(['Evidence point','模型','病种','类型','摘要','locator'], ev.map(x=>[esc(x.point_id),esc(x.model_key),esc(x.disease_slug),esc(x.point_type),"),
    ("table(['???','?????','??','??'], [[esc(c.bundle_id)",
     "table(['参数包','参数可信分','来源层级','状态'], [[esc(c.bundle_id)"),
    ("table(['??','??'], [['Baseline','BASELINE_FORBIDDEN_BY_POLICY??????????? top candidate'],['????','preexecution_evidence_score <= 70 ? parameter_confidence_score <= 70'],['metadata-only','parameter_evidence_count=0?score_contribution=0']",
     "table(['类别','原因'], [['Baseline','BASELINE_FORBIDDEN_BY_POLICY（禁止 baseline 成为 top candidate）'],['分数门槛','preexecution_evidence_score <= 70 或 parameter_confidence_score <= 70'],['metadata-only','parameter_evidence_count=0，score_contribution=0']"),
    ("table(['?????','?'], Object.entries(", "table(['执行断言','值'], Object.entries("),
    ("table(['?','??'], [['DATA_PROFILE ? MATCHED_TO ? MODEL_CONFIGURATION_CANDIDATE','????????'],['MODEL_CONFIGURATION_CANDIDATE ? USES_BLUEPRINT','??????????'],['MODEL_CONFIGURATION_CANDIDATE ? HAS_SCORE_COMPONENT','A?F ????'],['SCORE_COMPONENT ? SUPPORTED_BY/LIMITED_BY ? EVIDENCE_POINT','???????']]",
     "table(['边','说明'], [['DATA_PROFILE → MATCHED_TO → MODEL_CONFIGURATION_CANDIDATE','数据画像匹配到候选'],['MODEL_CONFIGURATION_CANDIDATE → USES_BLUEPRINT','候选使用模型蓝图'],['MODEL_CONFIGURATION_CANDIDATE → HAS_SCORE_COMPONENT','A–F 维度组件'],['SCORE_COMPONENT → SUPPORTED_BY/LIMITED_BY → EVIDENCE_POINT','证据点支持或限制']]"),
    ("$('kgStatus').textContent='???'+e.message;", "$('kgStatus').textContent='加载失败：'+e.message;"),
]

def apply(path, pairs):
    text = io.open(path, encoding="utf-8").read()
    misses = []
    for old, new in pairs:
        if old not in text:
            misses.append(old[:60])
            continue
        assert text.count(old) == 1, f"non-unique: {old[:50]}"
        text = text.replace(old, new)
    io.open(path, "w", encoding="utf-8", newline="").write(text)
    return misses

m1 = apply("USER_HOME/projects/openclaw/src/epidemic_core/forecast_model_kg/dashboard_app.py", PAIRS_PY)
m2 = apply("USER_HOME/projects/openclaw/src/epidemic_core/dashboard/static/forecast-model-kg.js", PAIRS_JS)
print("misses_py:", m1)
print("misses_js:", m2)
