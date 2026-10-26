(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function getJSON(url){const r=await fetch(url,{credentials:'same-origin'}); const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d;}
  function table(headers,rows){return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function unique(a){return [...new Set(a.filter(Boolean))].sort();}
  let catalog=[];

  const diseaseReasons={
    'covid-19':'COVID-19 报告序列同时受传播波动、报告延迟和历史修订影响',
    'influenza':'流感监测序列具有明显季节性，并受送检量和监测体系变化影响',
    'dengue':'登革热周度报告通常是过度离散计数，并可能包含较多零值、漏报和修订',
    'ebola-disease':'埃博拉数据多为稀疏事件或不规则累计报告，不能按普通连续周序列处理',
    'hantavirus':'Puumala/hantavirus 报告具有季节性和生态驱动特征，需保留稀疏计数结构',
    'legionella':'军团菌病报告是监测过程计数，季节性、暴露环境和报告延迟都可能影响曲线',
    'nipah':'Nipah 记录属于极稀疏事件数据，单次暴发与输入事件比常规时序趋势更重要'
  };
  const modelReasons={
    'Dynamic hurdle count state-space':'动态 hurdle 状态空间模型能分别描述“是否出现病例”和“出现后的病例数量”，适合零值与过度离散并存的计数序列',
    'Negative-Binomial DGLM':'负二项动态广义线性模型能表达病例计数的过度离散，并允许潜在水平随时间变化',
    'Gamma-Poisson DGLM':'Gamma-Poisson 动态模型适合非负计数，可通过共轭更新表达时变发生率与不确定性',
    'Bayesian local level':'贝叶斯局部水平模型适合以平滑潜在状态表示缓慢变化的监测水平，并输出概率区间',
    'Bayesian local trend':'贝叶斯局部趋势模型可同时描述当前水平和短期方向变化，适合趋势逐步漂移的序列',
    'Dynamic beta-binomial proportion':'动态 beta-binomial 模型适合阳性率等比例指标，并保留分母变化带来的不确定性',
    'Renewal (Cori R_t)':'更新方程模型用于有明确发生时间和代际间隔假设的新增病例序列，可估计传播强度而不是简单外推累计值',
    'Poisson event-rate (Gamma prior)':'Poisson 事件率模型用于极稀疏独立事件，以 Gamma 先验稳定估计发生率，不伪造连续疫情曲线'
  };
  const countryZh={AUT:'奥地利',BGD:'孟加拉国',BRA:'巴西',COD:'刚果民主共和国',FIN:'芬兰',FRA:'法国',PER:'秘鲁'};
  const diseaseZh={'covid-19':'新型冠状病毒感染','influenza':'流感','dengue':'登革热','ebola-disease':'埃博拉病毒病','hantavirus':'汉坦病毒病','legionella':'军团菌病','nipah':'尼帕病毒病'};
  const indicatorZh={cases_new:'新增报告病例数',deaths_new:'新增报告死亡数',cases_cumulative:'累计病例数',deaths_cumulative:'累计死亡数',legionnaires_reported_cases:'军团菌病报告病例数',specimens_processed_positive_total:'阳性标本数'};
  const frequencyZh={day:'每日',week:'每周',month:'每月',irregular:'不规则报告'};
  const statusZh={ELIGIBLE_RECOMMENDATION:'已通过执行前证据门槛',BELOW_THRESHOLD:'证据尚未达到推荐门槛',HARD_GATED:'不满足基本条件',DEFERRED_NOT_EXECUTED:'尚未执行模型',TRUSTED_LIVE:'可信正式数据',INCIDENT:'新增事件/病例',CUMULATIVE:'累计值'};
  const modelZh={
    'Dynamic hurdle count state-space':'Hurdle 动态计数状态空间模型',
    'Negative-Binomial DGLM':'负二项动态广义线性模型',
    'Gamma-Poisson DGLM':'Gamma–Poisson 动态广义线性模型',
    'Bayesian local level':'贝叶斯局部水平模型',
    'Bayesian local trend':'贝叶斯局部趋势模型',
    'Dynamic beta-binomial proportion':'动态 Beta–Binomial 比例模型',
    'Renewal (Cori R_t)':'更新方程传播模型（Cori Rₜ）',
    'Poisson event-rate (Gamma prior)':'Poisson 事件率模型（Gamma 先验）'
  };
  const dimensionMeta={
    A_data_structure_fit:['A 数据结构适配','当前数据的类型、长度、零值和时间间隔是否适合该模型'],
    B_disease_mechanism_fit:['B 疾病与指标机制','模型假设是否符合该病种及当前监测指标的形成过程'],
    C_country_scenario_transfer:['C 国家与监测场景','模型能否迁移到当前国家和当前数据质量场景'],
    D_scientific_evidence:['D 科学文献证据','是否有同病种、传染病和参数层面的全文证据支持'],
    E_parameter_confidence:['E 参数可信度','参数是否完整，并且能追溯到可信来源'],
    F_execution_validation_readiness:['F 执行与验证准备度','代码适配器、数据量、区间输出和回测条件是否已经具备'],
    PENALTY:['风险扣分项','当时间缺口、历史修订等风险可能影响模型可靠性时，从总分中扣除相应分数']
  };
  const ruleLabels={
    A1_DISTRIBUTION_MATCH:'病例分布是否匹配',A2_ZERO_INFLATION:'零值是否需要特殊处理',A3_LENGTH:'数据量是否足够',A4_FREQUENCY_REGULARITY:'时间间隔是否规则',
    B1_MECHANISM_CLASS:'模型机制是否合适',B2_INDICATOR_APPLICABILITY:'是否适用于当前指标',C1_GEO_INDEPENDENCE:'是否依赖特定国家参数',C2_SURVEILLANCE_QUALITY:'监测数据质量',
    D1_SAME_DISEASE_FULLTEXT:'同病种全文证据',D2_INFECTIOUS_DISEASE_FULLTEXT:'其他传染病全文证据',D3_PARAMETER_FULLTEXT:'参数全文证据',E1_BUNDLE_CONFIDENCE:'参数包可信程度',
    F1_ADAPTER_EXECUTABLE:'是否已有可执行适配器',F2_DATA_REQUIREMENTS:'是否满足最低数据量',F3_PROBABILISTIC_OUTPUT:'是否能输出不确定性区间',F4_BACKTEST_FEASIBLE:'是否具备滚动回测条件',P_IRREGULAR_CADENCE:'时间间隔不规则扣分',P_REVISION_HISTORY:'历史数据修订风险扣分'
  };
  function labelWithCode(label,code){return `${label}（${code}）`;}
  function displayCountry(v){return countryZh[v]?labelWithCode(countryZh[v],v):v;}
  function displayDisease(v){return diseaseZh[v]?labelWithCode(diseaseZh[v],v):String(v||'').replaceAll('_',' ');}
  function displayIndicator(v){return indicatorZh[v]?labelWithCode(indicatorZh[v],v):String(v||'').replaceAll('_',' ');}
  function displayFrequency(v){return frequencyZh[v]?labelWithCode(frequencyZh[v],v):v;}
  function displayModel(v){return modelZh[v]?labelWithCode(modelZh[v],v):v;}
  function displayStatus(v){return statusZh[v]?labelWithCode(statusZh[v],v):v;}
  function displayStatusPlain(v){return statusZh[v]||v;}
  function scoreState(score,max){if(Number(max)<=0&&Number(score)<0)return '已触发风险扣分';if(Number(score)>=Number(max))return '充分';if(Number(score)<=0)return '缺失';return '部分满足';}
  function readableRaw(x){const raw=String(x.raw_value??'');if(x.rule_id==='A1_DISTRIBUTION_MATCH')return raw.replace('sem=INCIDENT;overdisp=','新增病例语义；方差/均值比=').replace('sem=CUMULATIVE;overdisp=','累计值语义；方差/均值比=');if(x.rule_id==='A2_ZERO_INFLATION')return `零值比例 ${raw}`;if(x.rule_id==='A3_LENGTH'||x.rule_id==='F4_BACKTEST_FEASIBLE')return `${raw} 条观测`;if(x.rule_id==='A4_FREQUENCY_REGULARITY')return displayFrequency(raw);if(x.rule_id==='B1_MECHANISM_CLASS'&&raw==='OBSERVATION_PROCESS')return '监测报告形成过程';if(x.rule_id==='B2_INDICATOR_APPLICABILITY')return displayIndicator(raw);if(x.rule_id==='C1_GEO_INDEPENDENCE')return displayCountry(raw);if(x.rule_id==='C2_SURVEILLANCE_QUALITY')return statusZh[raw]||raw;if(x.rule_id==='D1_SAME_DISEASE_FULLTEXT')return `${raw} 个同病种证据点`;if(x.rule_id==='D2_INFECTIOUS_DISEASE_FULLTEXT')return `${raw} 个传染病证据点`;if(x.rule_id==='D3_PARAMETER_FULLTEXT')return `${raw} 个参数证据点`;if(x.rule_id==='E1_BUNDLE_CONFIDENCE')return `参数可信度 ${raw}`;if(x.rule_id==='F2_DATA_REQUIREMENTS'){const parts=raw.split('>=');return parts.length===2?`${parts[0]} 条观测，最低需要 ${parts[1]} 条`:raw;}if(x.rule_id==='F3_PROBABILISTIC_OUTPUT')return raw==='True'?'是':'否';if(x.rule_id==='P_IRREGULAR_CADENCE')return '当前序列存在时间缺口或不规则间隔';if(x.rule_id==='P_REVISION_HISTORY')return '当前序列存在历史修订';return raw;}
  function ruleExplanation(x){
    const raw=esc(readableRaw(x));
    const generic=`当前判断为“${scoreState(x.score,x.max_score)}”，审计值：${raw}。`;
    const messages={
      A1_DISTRIBUTION_MATCH:'检查病例数的均值、方差和数据语义是否符合模型的概率分布假设。',A2_ZERO_INFLATION:'检查零值比例，判断是否需要专门的零膨胀或 hurdle 结构。',A3_LENGTH:'检查历史观测数量是否达到该模型的最低要求。',A4_FREQUENCY_REGULARITY:'检查数据是否按固定时间间隔连续记录，以及是否存在缺口。',
      B1_MECHANISM_CLASS:'检查模型描述的是传播过程、监测过程还是事件发生率，并与当前任务匹配。',B2_INDICATOR_APPLICABILITY:'检查模型能否直接处理当前指标，避免把累计值、比例或事件数据当作普通新增病例。',C1_GEO_INDEPENDENCE:'检查模型结构是否依赖特定国家参数，避免不加验证地跨国家照搬。',C2_SURVEILLANCE_QUALITY:'检查数据是否来自可信、持续运行且可追溯的监测来源。',
      D1_SAME_DISEASE_FULLTEXT:'统计直接研究当前病种且能定位到论文原文的证据。',D2_INFECTIOUS_DISEASE_FULLTEXT:'统计其他传染病中可迁移的方法证据；它不能替代同病种证据。',D3_PARAMETER_FULLTEXT:'检查参数选择是否有论文全文或明确来源支持。',E1_BUNDLE_CONFIDENCE:'综合参数完整度、来源层级和可复算性，衡量参数方案是否可信。',
      F1_ADAPTER_EXECUTABLE:'确认项目中已有对应的、版本固定的模型适配器。',F2_DATA_REQUIREMENTS:'确认可用观测数量达到模型运行的最低门槛。',F3_PROBABILISTIC_OUTPUT:'确认模型能够给出不确定性区间，而不只是一个点预测。',F4_BACKTEST_FEASIBLE:'确认历史长度足以进行无未来泄漏的滚动回测。',P_IRREGULAR_CADENCE:'当前序列存在时间缺口或不规则间隔，因此对模型推荐分数进行保守扣减。',P_REVISION_HISTORY:'历史报告存在后续修订，早期数值可能变化，因此对模型推荐分数进行保守扣减。'
    };
    return `${messages[x.rule_id]||'检查该项是否满足当前模型的执行前条件。'} ${generic}`;
  }
  function renderScoreCards(comps){
    const grouped={}; comps.forEach(x=>(grouped[x.dimension]??=[]).push(x));
    return `<div class="kg-score-grid">${Object.entries(grouped).map(([dimension,items])=>{const meta=dimensionMeta[dimension]||[dimension,''];const score=items.reduce((n,x)=>n+Number(x.score||0),0);const max=items.reduce((n,x)=>n+Number(x.max_score||0),0);const pct=max?Math.round(score/max*100):0;const scoreLabel=dimension==='PENALTY'?`扣除 ${Math.abs(score)} 分`:`${score}/${max} 分`;return `<section class="kg-score-card"><div class="kg-score-head"><h3>${esc(meta[0])}</h3><strong>${scoreLabel}</strong></div><p>${esc(meta[1])}</p><div class="kg-score-bar"><span style="width:${pct}%"></span></div><ul>${items.map(x=>`<li><b>${esc(ruleLabels[x.rule_id]||x.rule_id)}</b><span class="kg-score-chip">${dimension==='PENALTY'?`扣 ${Math.abs(Number(x.score||0))} 分`:`${esc(x.score)}/${esc(x.max_score)}`}</span><p>${ruleExplanation(x)}</p><details><summary>查看原始评分记录</summary><code>${esc(x.rule_id)}</code><br>${esc(x.rationale)}</details></li>`).join('')}</ul></section>`;}).join('')}</div>`;
  }
  function evidenceCard(x,index,roles){const excerpt=String(x.raw_anchor||'');const purpose=(roles[String(x.evidence_span_id)]||['模型或参数适用性证据']).join('、');return `<article class="kg-evidence-card"><div class="kg-evidence-head"><b>证据 ${index+1}</b><span>第 ${esc(x.printed_page_label||x.physical_page||'未知')} 页</span></div><p class="kg-evidence-purpose"><b>用于支持：</b>${esc(purpose)}</p><p>${esc(excerpt.slice(0,220))}${excerpt.length>220?'…':''}</p><details><summary>展开英文原文和精确审计定位</summary><p class="kg-translation-note">为避免误译改变证据含义，原文保持论文语言；上方中文仅说明它参与了哪项评分。</p><blockquote>${esc(excerpt)}</blockquote><dl><dt>章节</dt><dd>${esc(x.section_label||'原文未可靠识别')}</dd><dt>文本偏移</dt><dd>${esc(`${x.anchor_start}–${x.anchor_end}`)}</dd><dt>证据编号</dt><dd><code>${esc(x.evidence_span_id)}</code></dd><dt>原文哈希</dt><dd><code>${esc(x.anchor_sha256)}</code></dd></dl></details></article>`;}
  function renderEvidence(ev,comps){if(!ev.length)return '<p class="kg-empty">当前推荐没有绑定可展示的精确全文证据；该缺口不会被伪装成证据。</p>';const roles={};(comps||[]).forEach(c=>(c.source_ids||[]).forEach(id=>{const key=String(id);(roles[key]??=[]).push(ruleLabels[c.rule_id]||c.rule_id);}));Object.keys(roles).forEach(k=>roles[k]=unique(roles[k]));const initial=ev.slice(0,6).map((x,i)=>evidenceCard(x,i,roles)).join('');const rest=ev.slice(6).map((x,i)=>evidenceCard(x,i+6,roles)).join('');return `<div class="kg-evidence-summary"><b>已关联 ${ev.length} 条可定位证据</b><span>默认显示前 ${Math.min(6,ev.length)} 条</span></div><div class="kg-evidence-grid">${initial}</div>${rest?`<details class="kg-evidence-more"><summary>查看其余 ${ev.length-6} 条证据</summary><div class="kg-evidence-grid">${rest}</div></details>`:''}`;}
  function compactInfo(rows){return `<dl class="kg-info-list">${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;}
  function explainRecommendation(x){
    const family=x.model_family||x.adapter_id||'当前模型';
    const disease=diseaseReasons[x.disease]||`${x.disease} 数据具有其特定的报告与传播过程`;
    const model=modelReasons[family]||`${family} 与当前数据画像的结构门禁相匹配`;
    const eligibility=x.eligibility_status==='ELIGIBLE_RECOMMENDATION'?'已通过执行前证据门槛':'尚未通过执行前证据门槛';
    return `${disease}；${model}。当前总分 ${x.total_score}、参数可信度 ${x.parameter_confidence_score}，${eligibility}；尚未训练、回测或生成预测值。`;
  }
  function fillSelect(id,values,labeler=v=>v){const el=$(id),old=el.value; el.innerHTML=values.map(v=>`<option value="${esc(v)}">${esc(labeler(v))}</option>`).join(''); if(values.includes(old))el.value=old;}
  function refreshSelectors(from){
    if(from==='country'||!$('kgDisease').value) fillSelect('kgDisease',unique(catalog.filter(x=>x.country===$('kgCountry').value).map(x=>x.disease)),displayDisease);
    if(from==='country'||from==='disease'||!$('kgMetric').value) fillSelect('kgMetric',unique(catalog.filter(x=>x.country===$('kgCountry').value&&x.disease===$('kgDisease').value).map(x=>x.indicator)),displayIndicator);
    fillSelect('kgCadence',unique(catalog.filter(x=>x.country===$('kgCountry').value&&x.disease===$('kgDisease').value&&x.indicator===$('kgMetric').value).map(x=>x.frequency)),displayFrequency);
  }
  function clearDetail(message){
    ['kgDirectAnswer','kgWaterfall','kgMatrix','kgEvidence','kgParams','kgTableFallback','kgExcluded','kgDeferred'].forEach(id=>{$(id).innerHTML=`<p class="kg-empty">${esc(message)}</p>`;});
    $('kgGraph').innerHTML='';
  }
  function detailError(error){
    clearDetail(`明细加载失败：${error.message}。请刷新后重试；错误已保留在服务日志中。`);
  }
  async function loadCatalog(){
    const d=await getJSON('/api/v1/kg/r3c/catalog'); catalog=d.items||[];
    fillSelect('kgCountry',unique(catalog.map(x=>x.country)),displayCountry);
    refreshSelectors('country');
  }
  async function loadCandidates(){
    const q=new URLSearchParams({country:$('kgCountry').value,disease:$('kgDisease').value,indicator:$('kgMetric').value,frequency:$('kgCadence').value,limit:'200'});
    const d=await getJSON('/api/v1/kg/r3c/recommendations?'+q);
    const allItems=d.items||[];
    const items=allItems.filter(item=>item.eligibility_status!=='HARD_GATED');
    $('kgStatus').innerHTML=`已找到 <b>${items.length}</b> 个可评估模型候选。当前状态：<b>${esc(displayStatusPlain(d.execution_status))}</b>；以下分数表示证据充分程度，不是预测准确率。`;
    if(!items.length){$('kgModels').textContent='当前组合没有模型候选。'; clearDetail('当前组合没有可展示的推荐明细。'); return;}
    const rows=items.map(x=>[`<button data-rid="${esc(x.recommendation_id)}" class="kg-detail">查看明细</button>`,esc(displayModel(x.model_family||x.adapter_id)),`<b>${esc(x.total_score)}</b> / 100`,`${esc(x.parameter_confidence_score)} / 100`,esc(displayStatusPlain(x.eligibility_status))]);
    $('kgModels').innerHTML=table(['操作','模型','证据总分','参数可信度','当前结论'],rows);
    document.querySelectorAll('.kg-detail').forEach(b=>b.addEventListener('click',()=>loadDetail(b.dataset.rid)));
    await loadDetail(items[0].recommendation_id);
  }
  async function loadDetail(rid){
    try{
      const d=await getJSON('/api/v1/kg/r3c/recommendations/'+encodeURIComponent(rid));
      const r=d.recommendation||{}, comps=d.components||[], ev=d.evidence_spans||[];
      const profileResponse=await getJSON('/api/v1/kg/data-profiles/'+encodeURIComponent(r.profile_id));
      const p=profileResponse.profile||{}, task=profileResponse.forecast_task||{};
      const bundle=d.bundle||{}, adapter=d.adapter||{};
      const model=adapter.model_family||r.adapter_id||'当前模型';
      $('kgDirectAnswer').innerHTML=`<div class="kg-answer-box"><div class="kg-answer-title"><strong>${esc(displayModel(model))}</strong><span class="kg-result-badge">${esc(displayStatusPlain(r.eligibility_status))}</span></div><p>${esc(explainRecommendation({...r,model_family:model}))}</p><p class="kg-plain-note">简单理解：系统先判断当前数据“长什么样”，再核对疾病机制、论文证据、参数来源和运行条件。当前模型只是证据上更匹配，仍需真实回测后才能决定是否采用。</p></div>`;
      $('kgWaterfall').innerHTML=renderScoreCards(comps);
      $('kgMatrix').innerHTML=compactInfo([
        ['国家',esc(displayCountry(r.country))],['病种',esc(displayDisease(r.disease))],['指标',esc(displayIndicator(r.indicator))],['记录频率',esc(displayFrequency(r.frequency))],
        ['历史观测数',`<b>${esc(p.n_obs)}</b> 条`],['数据时间范围',`${esc(p.first_date)} 至 ${esc(p.last_date)}`],['预测步长',esc((task.horizons||[]).join('、')||'未设置')],['目标语义',esc(displayStatusPlain(task.target_semantics)||task.target_semantics)]
      ])+`<details class="kg-audit-details"><summary>查看数据审计标识</summary>${table(['审计字段','值'],[['数据来源表',p.source_table],['数据画像哈希',p.profile_sha256],['快照哈希',p.snapshot_sha256],['防未来泄漏边界','仅使用预测起点及以前的观测；不使用未来协变量'],['原始边界记录',task.leakage_boundary]].map(row=>row.map(esc)))}</details>`;
      $('kgEvidence').innerHTML=renderEvidence(ev,comps);
      const tiers=(bundle.source_tiers||[]).join('、')||'未记录';
      $('kgParams').innerHTML=compactInfo([
        ['模型实现',esc(displayModel(model))],['适配器版本',esc(adapter.adapter_version||'未记录')],['参数可信度',`<b>${esc(r.parameter_confidence_score)}</b> / 100`],['参数完整度',`${Math.round(Number(bundle.completeness||0)*100)}%`],['证据来源层级',esc(tiers)],['已有可执行实现',adapter.executable?'是':'否']
      ])+`<details class="kg-audit-details"><summary>查看完整参数和审计标识</summary><pre>${esc(JSON.stringify(bundle.parameters||{},null,2))}</pre>${table(['审计字段','值'],[['参数包编号',bundle.bundle_id],['参数包版本',bundle.bundle_version],['参数包哈希',bundle.bundle_sha256],['适配器编号',adapter.adapter_id]].map(row=>row.map(esc)))}</details>`;
      const reasons=r.ineligibility_reasons||[],gaps=r.gap_list||[];
      $('kgExcluded').innerHTML=compactInfo([
        ['当前结论',esc(displayStatusPlain(r.eligibility_status))],['未通过原因',reasons.length?`存在 ${reasons.length} 项，见下方技术明细`:'无'],['证据缺口',gaps.length?`存在 ${gaps.length} 项，见下方技术明细`:'无'],['基线模型用途','只用于回测比较，不作为知识图谱正式推荐'],['严格门槛','证据总分 > 70，且参数可信度 > 70']
      ])+`<details class="kg-audit-details"><summary>查看原始门禁代码</summary><pre>${esc(JSON.stringify({ineligibility_reasons:reasons,gap_list:gaps},null,2))}</pre></details>`;
      $('kgDeferred').innerHTML=compactInfo([
        ['模型是否已经运行','否'],['是否已经生成预测结果','否'],['预测运行编号','无'],['当前状态',esc(displayStatusPlain(d.execution_status))],['安全边界','本页只提供执行前模型建议，不构成公共卫生决策或医疗建议']
      ])+`<details class="kg-audit-details"><summary>查看机器审计断言</summary><pre>${esc(JSON.stringify({score_type:d.score_type,execution_status:d.execution_status,actually_executed:r.actually_executed,forecast_run_id:r.forecast_run_id,audit_statement:d.audit_statement},null,2))}</pre></details>`;
      drawGraph(r,d,ev);
    }catch(error){detailError(error);}
  }
  function drawGraph(r,d,ev){
    const svg=$('kgGraph'); svg.innerHTML=''; svg.setAttribute('viewBox','0 0 900 400'); const ns='http://www.w3.org/2000/svg';
    const nodes=[
      {x:150,y:105,title:'当前数据画像',sub:'国家・病种・指标・频率'},
      {x:450,y:105,title:'模型证据推荐',sub:'六维评分与门禁'},
      {x:750,y:105,title:'模型实现',sub:'固定版本适配器'},
      {x:150,y:295,title:'参数方案',sub:'来源与可信度'},
      {x:450,y:295,title:'知识图谱版本',sub:'文献和关系版本固定'},
      {x:750,y:295,title:'论文原文证据',sub:`${ev.length} 条精确定位`}
    ];
    [[0,1],[1,2],[1,3],[1,4],[4,5]].forEach(([a,b])=>{const line=document.createElementNS(ns,'line');line.setAttribute('x1',nodes[a].x);line.setAttribute('y1',nodes[a].y);line.setAttribute('x2',nodes[b].x);line.setAttribute('y2',nodes[b].y);line.setAttribute('class','kg-edge');svg.append(line);});
    nodes.forEach(n=>{const g=document.createElementNS(ns,'g');const rect=document.createElementNS(ns,'rect');rect.setAttribute('x',n.x-105);rect.setAttribute('y',n.y-44);rect.setAttribute('width',210);rect.setAttribute('height',88);rect.setAttribute('rx',14);rect.setAttribute('class','kg-node-box');const title=document.createElementNS(ns,'text');title.setAttribute('x',n.x);title.setAttribute('y',n.y-7);title.setAttribute('text-anchor','middle');title.setAttribute('class','kg-graph-title');title.textContent=n.title;const sub=document.createElementNS(ns,'text');sub.setAttribute('x',n.x);sub.setAttribute('y',n.y+19);sub.setAttribute('text-anchor','middle');sub.setAttribute('class','kg-graph-subtitle');sub.textContent=n.sub;g.append(rect,title,sub);svg.append(g);});
    $('kgTableFallback').innerHTML=`<details class="kg-audit-details"><summary>用表格查看图中关系</summary>${table(['关系','通俗说明'],[['数据画像 → 模型推荐','根据当前数据结构进行确定性的执行前评分'],['模型推荐 → 模型实现','推荐结果绑定固定版本、可审计的模型适配器'],['模型推荐 → 参数方案','参数值和来源层级通过哈希固定'],['模型推荐 → 知识图谱版本','推荐绑定已发布的论文知识图谱版本'],['评分依据 → 论文原文证据',ev.length?`关联 ${ev.length} 条带页码、偏移和哈希的原文证据`:'当前没有精确全文证据定位']].map(row=>row.map(esc)))}</details>`;
  }
  $('kgCountry').addEventListener('change',()=>refreshSelectors('country'));
  $('kgDisease').addEventListener('change',()=>refreshSelectors('disease'));
  $('kgMetric').addEventListener('change',()=>refreshSelectors('metric'));
  $('kgRun').addEventListener('click',()=>loadCandidates().catch(error=>{$('kgStatus').textContent='加载失败：'+error.message; detailError(error);}));
  loadCatalog().then(loadCandidates).catch(error=>{$('kgStatus').textContent='加载失败：'+error.message; detailError(error);});
})();

/* kg-r3c section: self-contained loader for the R3C panel on /forecast-model-knowledge-graph.
   Guarded by #kg-r3c presence; failures here never touch the pre-execution sections above. */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const root=$('kg-r3c'); if(!root) return;
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const statusText={PUBLISHED:'已发布',SUPERSEDED:'已被新版本替代',OPEN:'处理中',COMPLETED:'已完成',QUARANTINED:'已隔离',PENDING_HUMAN_REVIEW:'等待人工复核',APPROVED:'已批准',REJECTED:'已拒绝'};
  const zhStatus=v=>statusText[v]?`${statusText[v]}（${v}）`:v;
  async function getJSON(url){const r=await fetch(url,{credentials:'same-origin'}); const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d;}
  function table(headers,rows){return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function fail(id,e){$(id).innerHTML=`<p class="kg-error">加载失败：${esc(e.message)}（不影响本页其他区块）</p>`;}
  async function loadImports(){
    try{
      const d=await getJSON('/api/v1/kg/import-runs');
      const rows=(d.items||[]).map(x=>[esc(x.import_key),esc(zhStatus(x.status)),esc(x.member_count),esc(x.quarantined_count),esc(x.started_at)]);
      $('kgR3cImports').innerHTML=rows.length?table(['导入批次','状态','文件成员数','安全隔离数','开始时间'],rows):'<p>暂无导入记录</p>';
    }catch(e){fail('kgR3cImports',e);}
  }
  async function loadGraph(){
    try{
      const d=await getJSON('/api/v1/kg/graph-versions?limit=1');
      const v=(d.items||[])[0];
      if(!v){$('kgR3cGraph').textContent='暂无图版本'; return;}
      const ir=v.integrity_report||{};
      const integrity=ir.integrity||ir;
      $('kgR3cGraph').innerHTML=table(['项目','值'],[
        ['知识图谱版本编号',esc(v.graph_version_id)],
        ['状态',esc(zhStatus(v.status))],
        ['发布时间',esc(v.published_at)],
        ['文档集合哈希',esc(String(v.document_set_hash||'').slice(0,16)+'…')],
        ['知识断言集合哈希',esc(String(v.assertion_set_hash||'').slice(0,16)+'…')],
        ['完整性检查',integrity.pass===true?'通过':'需要复核'],
      ].map(r=>r.map(x=>x)))+`<details class="kg-audit-details"><summary>查看完整机器校验记录</summary><pre>${esc(JSON.stringify(ir,null,2))}</pre></details>`;
    }catch(e){fail('kgR3cGraph',e);}
  }
  async function loadReview(){
    try{
      const d=await getJSON('/api/v1/kg/review-items?limit=10');
      const counts=d.status_counts||{};
      const countText=Object.entries(counts).map(([k,v])=>`${esc(zhStatus(k))}：${esc(v)}`).join('；')||'无';
      const rows=(d.items||[]).map(x=>[esc(x.item_kind),esc(x.reason),esc(zhStatus(x.status)),esc(x.created_at)]);
      $('kgR3cReview').innerHTML=`<p>复核状态：${countText}</p>`+(rows.length?table(['条目类型','进入复核的原因','状态','创建时间'],rows):'<p>暂无待复核条目</p>');
    }catch(e){fail('kgR3cReview',e);}
  }
  function start(){
    Promise.allSettled([loadImports(),loadGraph(),loadReview()]).then(results=>{
      const failed=results.filter(r=>r.status==='rejected').length;
      $('kgR3cStatus').textContent=failed?`R3C 数据部分加载失败（${failed}/3 个区块），其余区块不受影响`:'R3C 数据与复核状态加载完成；模型推荐统一显示在页面上方。';
    });
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
