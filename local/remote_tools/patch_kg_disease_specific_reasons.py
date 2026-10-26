from __future__ import annotations

from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")
JS = ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js"


def main() -> int:
    text = JS.read_text(encoding="utf-8")
    insert = r'''
  function diseaseSpecificReasons(data){
    const disease = data.resolved_disease || {};
    const profile = data.dataset_profile || {};
    const id = disease.id || '';
    const track = data.eligibility_track || 'UNKNOWN';
    const weeks = profile.history_weeks_normalized;
    const regions = profile.region_count;
    const target = profile.forecast_target || data.request?.forecast_target || 'cases_new';
    const reasons = [];
    if (id === 'influenza') {
      reasons.push('流感有强季节性和年度周期，因此需要能刻画季节波动的模型；状态空间 / Kalman 适合把趋势、季节项和短期噪声拆开处理。');
      reasons.push(`当前目标指标为 ${esc(target)}；如果是 ILI/SARI 这类监测指标，报告口径相对连续但可能受检测行为影响，所以必须同时比较最后观测值、季节性基线和移动平均基线。`);
      if (track === 'FORMAL') reasons.push('当历史长度和地区数足够时，正式判断不看单次拟合效果，而看多个 rolling-origin 上是否稳定优于基线。');
      if (track === 'DEMO') reasons.push('当前历史长度接近但未达到正式门槛，因此只能演示模型链路，不能声称生产级预测有效。');
    } else if (id === 'dengue') {
      reasons.push('登革热由蚊媒传播，病例变化通常受温度、降雨、湿度和媒介生态滞后影响；如果缺少气候/媒介协变量，应先用病例计数模型和季节性基线，不宜直接宣称环境驱动模型。');
      reasons.push('登革热容易出现地区性暴发和跨地区不同步，因此地区数量、地理覆盖和近期完整性会直接影响模型是否能正式发布。');
      reasons.push('如果未来接入天气或气候滞后变量，才适合把带外生变量的树模型或分层时空模型作为强候选。');
    } else if (id === 'legionella') {
      reasons.push('军团菌病常与环境暴露、供水系统、气温、降雨或湿度有关；仅有病例计数时，模型只能做病例趋势/基线判断，不能解释环境风险。');
      reasons.push('当前美国军团菌仍是 preview 数据，只有已验证周可展示，缺失周必须留空；因此知识图谱会先要求补齐 104 周来源验证，而不是直接训练预测。');
      reasons.push('当环境协变量和连续周度数据充足时，才适合比较滞后环境特征模型与季节性基线。');
    } else if (id === 'ebola') {
      reasons.push('Ebola 通常是稀疏、事件驱动、强干预影响的暴发序列；未报告周不能简单视为 0，也不适合用普通连续周度模型硬拟合。');
      reasons.push('更合适的是事件级态势摘要、场景风险说明、增长率/倍增时间的受控估计，以及人工复核后的 outbreak phase 标注。');
      reasons.push('如果出现连续报告期，也必须单独限定在同一次疫情事件内评估，不能把不同国家和不同暴发直接拼成一条稳定季节序列。');
    } else if (id === 'nipah') {
      reasons.push('Nipah 是低频高风险的人畜共患事件，数据缺失通常代表未知而不是零；因此知识图谱优先推荐事件级风险说明，而非连续病例预测。');
      reasons.push('模型原因重点不在季节曲线拟合，而在宿主、暴露场景、聚集性传播和人工复核证据。');
      reasons.push('只有在明确的事件窗口内，才可做短期场景分析或风险分级，不能发布常规周度预测。');
    } else if (id === 'hantavirus_puumala') {
      reasons.push('Puumala/Hantavirus 与啮齿动物宿主、森林生态、气候和人群暴露相关；没有生态协变量时，不能把芬兰 Puumala 经验泛化为所有汉坦病毒地区。');
      reasons.push('如果数据足够且生态变量可用，模型应强调外生协变量和滞后效应；否则只能做保守的病例基线或风险提示。');
      reasons.push('推荐原因必须绑定具体国家和宿主生态，不应只根据病种名称给全球统一模型。');
    } else if (id === 'covid_19') {
      reasons.push('COVID-19 受报告政策、检测强度、变异株和免疫背景影响明显；模型需要处理结构突变和报告口径变化，不能只依赖平滑时间序列。');
      reasons.push('如果病例数据连续且近期完整，可以用状态空间、树模型或基线组合做短期预测，但必须检查政策/检测变化导致的数据断点。');
      reasons.push('正式发布前必须证明 cutoff 之后数据不会泄漏进特征，并用近期 rolling-origin 验证。');
    } else {
      reasons.push('当前病种使用通用传染病时间序列规则：先判断历史长度、近期完整性、地区数、目标指标和质量门禁，再选择候选模型。');
      reasons.push('模型不能只因能运行就发布，必须先和简单基线比较并通过回测。');
    }
    if (data.dataset_profile_source === 'user_supplied_profile') reasons.unshift('这是 FORMAL 演示画像，用于展示知识图谱规则如何工作；它不代表当前数据库真实数据已经满足正式发布条件。');
    if (weeks !== null && weeks !== undefined) reasons.push(`当前画像历史长度约 ${esc(weeks)} 周，这是判断 DEMO/FORMAL 的关键依据。`);
    if (regions !== null && regions !== undefined) reasons.push(`当前画像地区/序列数为 ${esc(regions)}，会影响模型是否具备跨地区稳定性检验条件。`);
    return reasons;
  }
'''
    if "function diseaseSpecificReasons(data)" not in text:
        text = text.replace("  function directAnswer(data){", insert + "\n  function directAnswer(data){")

    old = """    const reasonItems = (data.reasons || []).slice(0, 3).map(x => `规则原因：${esc(x)}`);
    if (warnings.length) reasonItems.push(`限制条件：${esc(warnings[0])}`);
    if (!reasonItems.length && track === 'FORMAL') reasonItems.push('数据长度、地区数量和质量门禁满足正式回测候选条件。');
    if (!reasonItems.length && track === 'DEMO') reasonItems.push('数据已有可用历史，但未达到正式生产级门槛。');
"""
    new = """    const reasonItems = diseaseSpecificReasons(data);
    if (warnings.length) reasonItems.push(`限制条件：${esc(warnings[0])}`);
"""
    if old not in text:
        raise RuntimeError("reason block anchor not found")
    text = text.replace(old, new)

    old_reasons_panel = """      $('kgReasons').innerHTML=(data.reasons||[]).map(x=>`<p>${esc(x)}</p>`).join('')+(data.warnings||[]).map(x=>`<p class="kg-error">Warning: ${esc(x)}</p>`).join('')+`<table class="kg-table"><tr><th>Profile field</th><th>Value</th></tr>${Object.entries(data.dataset_profile||{}).slice(0,18).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v===null?'unknown / not observed':Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table>`;"""
    new_reasons_panel = """      const specificReasons=diseaseSpecificReasons(data);
      $('kgReasons').innerHTML='<h3>中文推荐原因 / Disease-specific rationale</h3><ol>'+specificReasons.map(x=>`<li>${x}</li>`).join('')+'</ol>'+(data.reasons||[]).map(x=>`<p><strong>规则命中：</strong>${esc(x)}</p>`).join('')+(data.warnings||[]).map(x=>`<p class="kg-error">Warning: ${esc(x)}</p>`).join('')+`<table class="kg-table"><tr><th>Profile field</th><th>Value</th></tr>${Object.entries(data.dataset_profile||{}).slice(0,18).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v===null?'unknown / not observed':Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table>`;"""
    if old_reasons_panel not in text:
        raise RuntimeError("reasons panel anchor not found")
    text = text.replace(old_reasons_panel, new_reasons_panel)

    JS.write_text(text, encoding="utf-8")
    print("patch=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
