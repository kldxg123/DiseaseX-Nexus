(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  // Guard: no-op when the pre-execution section is absent, so later blocks run.
  if(!$('kgCountry')) return;
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
    return `<div class="kg-score-grid">${Object.entries(grouped).map(([dimension,items])=>{const meta=dimensionMeta[dimension]||[dimension,''];const score=items.reduce((n,x)=>n+Number(x.score||0),0);const max=items.reduce((n,x)=>n+Number(x.max_score||0),0);const pct=max?Math.round(score/max*100):0;const scoreLabel=dimension==='PENALTY'?`扣除 ${Math.abs(score)} 分`:`${score}/${max} 分`;return `<section class="kg-score-card"><div class="kg-score-head"><h3>${esc(meta[0])}</h3><strong>${scoreLabel}</strong></div><p>${esc(meta[1])}</p><div class="kg-score-bar"><span data-width="${pct}"></span></div><ul>${items.map(x=>`<li><b>${esc(ruleLabels[x.rule_id]||x.rule_id)}</b><span class="kg-score-chip">${dimension==='PENALTY'?`扣 ${Math.abs(Number(x.score||0))} 分`:`${esc(x.score)}/${esc(x.max_score)}`}</span><p>${ruleExplanation(x)}</p><details><summary>查看原始评分记录</summary><code>${esc(x.rule_id)}</code><br>${esc(x.rationale)}</details></li>`).join('')}</ul></section>`;}).join('')}</div>`;
  }
  function evidenceCard(x,index,roles){
    const excerpt=String(x.raw_anchor||'');
    const purpose=(roles[String(x.evidence_span_id)]||['模型或参数适用性证据']).join('、');
    const physical=x.physical_page??'未知';
    const printed=x.printed_page_label||'原文未标注';
    const start=x.anchor_start??'未知';
    const end=x.anchor_end??'未知';
    const locatorType=(x.anchor_start!==null&&x.anchor_start!==undefined&&x.anchor_end!==null&&x.anchor_end!==undefined)?'PAGE_TEXT_OFFSET':'PAGE_EXCERPT';
    return `<article class="kg-evidence-card"><div class="kg-evidence-head"><b>证据 ${index+1}</b><span>物理页 ${esc(physical)} · 印刷页 ${esc(printed)}</span></div><p class="kg-evidence-purpose"><b>用于支持：</b>${esc(purpose)}</p><p>${esc(excerpt.slice(0,220))}${excerpt.length>220?'…':''}</p><details><summary>展开英文原文和精确审计定位</summary><p class="kg-translation-note">原文保持论文语言，避免翻译改变证据含义。右侧定位字段可用于从 PDF/page text 反向复算；缺失字段会如实显示“未知”。</p><blockquote lang="en">${esc(excerpt)}</blockquote><dl><dt>定位类型</dt><dd><code>${esc(locatorType)}</code></dd><dt>物理页</dt><dd>${esc(physical)}</dd><dt>印刷页</dt><dd>${esc(printed)}</dd><dt>章节</dt><dd>${esc(x.section_label||'原文未可靠识别')}</dd><dt>页文本偏移</dt><dd><code>${esc(`${start}–${end}`)}</code></dd><dt>证据编号</dt><dd><code>${esc(x.evidence_span_id||'未知')}</code></dd><dt>文档版本</dt><dd><code>${esc(x.document_version_id||'未知')}</code></dd><dt>原文 SHA-256</dt><dd><code>${esc(x.anchor_sha256||'未知')}</code></dd><dt>源 PDF SHA-256</dt><dd><code>${esc(x.source_pdf_sha256||'未知')}</code></dd></dl></details></article>`;
  }
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
      document.querySelectorAll('#kgWaterfall .kg-score-bar span').forEach(s=>{s.style.width=(s.dataset.width||'0')+'%';});
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

/* Track E: immutable Singapore dengue paper-aligned replay.  This module is
   deliberately read-only and separate from the fresh held-out/Qwen runner. */
(()=>{
  'use strict';
  const root=document.getElementById('kg-paper-benchmark-e'); if(!root) return;
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
  const pct=v=>Number.isFinite(Number(v))?Number(v).toFixed(2)+'%':'—';
  const n=v=>Number.isFinite(Number(v))?Number(v).toFixed(4):'—';
  const short=v=>v?String(v).slice(0,16)+'…':'—';
  const table=(headers,rows)=>`<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  async function load(){
    const r=await fetch('/api/v1/forecast-model-knowledge-graph/benchmarks/sgp-dengue',{credentials:'same-origin'});
    const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'}));
    if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);
    $('kgBenchmarkEStatus').innerHTML=`<b>达标：</b>测试集预测吻合度 ${pct(d.metrics.test.agreement_pct)}，WAPE ${pct(Number(d.metrics.test.wape)*100)}。<b>身份：</b>${esc(d.evaluation_identity)}（非 Fresh 盲测）。`;
    $('kgBenchmarkEData').innerHTML=table(['字段','值'],[
      ['国家 / 病种 / 指标','SGP / dengue / cases_new'],['频率与范围',`${esc(d.frequency)}；${esc(d.source.range)}`],['规范化时点',esc(d.source.canonical_rows)],['训练 / 验证 / 测试',`${esc(d.split.training_labels)} / ${esc(d.split.validation)} / ${esc(d.split.test)}`],['数据 SHA-256',`<code>${esc(short(d.source.canonical_csv_sha256))}</code>`]
    ]);
    const comps=(d.knowledge_graph_recommendation.components||[]).map(x=>`${esc(x.model)} × ${pct(Number(x.weight)*100)}`).join('<br>');
    $('kgBenchmarkERecommendation').innerHTML=`<p>${esc(d.knowledge_graph_recommendation.reason_zh)}</p>`+table(['组成','权重'],[['验证集选择的组合',comps]]);
    $('kgBenchmarkEMetrics').innerHTML=table(['评估集','WAPE（越低越好）','预测吻合度（越高越好）','MAE','RMSE','70% 门槛'],[
      ['验证集',pct(Number(d.metrics.validation.wape)*100),pct(d.metrics.validation.agreement_pct),n(d.metrics.validation.mae),n(d.metrics.validation.rmse),'通过'],
      ['测试集',pct(Number(d.metrics.test.wape)*100),`<b>${pct(d.metrics.test.agreement_pct)}</b>`,n(d.metrics.test.mae),n(d.metrics.test.rmse),d.metrics.target_met?'<b>通过</b>':'未通过']
    ])+'<p class="kg-plain-note">预测吻合度 = max(0, 1 − WAPE) × 100%，不是分类准确率。组合权重仅使用验证集选择，测试标签在最终评估前未参与选模。</p>';
    $('kgBenchmarkEComparators').innerHTML=table(['方法','测试 WAPE','测试预测吻合度','测试 RMSE'],[
      ['2024 论文 AR(2)',pct(Number(d.comparators.paper_ar2_test.wape)*100),pct(d.comparators.paper_ar2_test.agreement_pct),n(d.comparators.paper_ar2_test.rmse)],
      ['Last-value 基线',pct(Number(d.comparators.last_value_test.wape)*100),pct(d.comparators.last_value_test.agreement_pct),n(d.comparators.last_value_test.rmse)],
      ['项目单模型',pct(Number(d.comparators.project_single_model_test.wape)*100),pct(d.comparators.project_single_model_test.agreement_pct),n(d.comparators.project_single_model_test.rmse)],
      ['项目验证集选择组合',pct(Number(d.metrics.test.wape)*100),`<b>${pct(d.metrics.test.agreement_pct)}</b>`,n(d.metrics.test.rmse)]
    ]);
    $('kgBenchmarkEAudit').innerHTML=`<p><b>${esc(d.governance_note_zh)}</b></p>`+table(['审计对象','SHA-256'],[
      ['官方响应',`<code>${esc(short(d.source.source_response_sha256))}</code>`],['规范化 CSV',`<code>${esc(short(d.source.canonical_csv_sha256))}</code>`],['逐行预测',`<code>${esc(short(d.reproducibility.paired_predictions_csv_sha256))}</code>`],['API 载荷',`<code>${esc(short(d.payload_sha256))}</code>`]
    ])+`<p class="kg-plain-note">发布状态：${esc(d.publication_state)}。论文：${esc(d.paper.title)}（${esc(d.paper.year)}，arXiv 预印本，非正式同行评审发表，LEGACY_PREPRINT_NOT_PRIMARY_BENCHMARK）。</p>`;
  }
  function start(){load().catch(e=>{$('kgBenchmarkEStatus').textContent='第三条链路加载失败：'+e.message;});}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
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

/* kg-dual-loop section: recommendation -> shadow execution -> backtest -> feedback loop.
   Guarded by #kg-dual-loop presence; failures here never touch other sections. */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const root=$('kg-dual-loop'); if(!root) return;
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function reqJSON(url,opts){const r=await fetch(url,{credentials:'same-origin',...(opts||{})}); const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d;}
  const getJSON=url=>reqJSON(url);
  function table(headers,rows){return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function compactInfo(rows){return `<dl class="kg-info-list">${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;}
  const modelZhName={'negbin_dglm.v1':'负二项动态广义线性模型','hurdle_count_ss.v1':'Hurdle 动态计数状态空间模型','last_value':'基线：上一期值','rolling_mean':'基线：滑动均值','drift':'基线：漂移趋势','seasonal_naive':'基线：季节朴素'};
  const championZh={'KG_RECOMMENDED_MODEL':'KG 推荐模型胜出','EMPIRICAL_BASELINE_OVERRIDE':'基线模型经验胜出（推荐模型被校正）'};
  const maturityZh={'MATURED':'已成熟（已有真实值）','AWAITING_ACTUAL':'等待官方真实值','SUPERSEDED_REVISION':'已被修订版本替代'};
  const DS={
    AUT:{country:'AUT',disease:'legionella',indicator:'legionnaires_reported_cases',frequency:'month',
         limits:'<b>历史拟真回放：</b>奥地利军团菌病数据止于 2024-12，属于较旧数据。本链路用于验证“推荐→回测→反馈→学习”机制，<b>不代表当前实时疫情</b>，也不构成任何公共卫生结论。'},
    PER:{country:'PER',disease:'influenza',indicator:'influenza_ili_cases',frequency:'week',
         limits:'<b>Demo Shadow：</b>秘鲁流感 ILI 序列仅约 53 周，<b>尚未验证跨年度季节性</b>；H1–H4 为面向未来的 Shadow 预测，等待官方新周数据到达后由对账入口自动核验。季节朴素基线因不足两个完整年度周期被标记为不适用（NOT_ELIGIBLE_INSUFFICIENT_TWO_CYCLES）。'}
  };
  let current=null; // {key, runId}
  function fmt(v,d=3){if(v===null||v===undefined||v==='')return '—'; const n=Number(v); return Number.isFinite(n)?String(Math.round(n*1000)/1000):esc(String(v));}
  function short(sha,n=12){return sha?esc(String(sha).slice(0,n))+'…':'—';}

  async function findRecommendation(key){
    const d=await getJSON('/api/v1/kg/forecast-eligible-series');
    const cfg=DS[key];
    const item=(d.items||[]).find(x=>x.country===cfg.country&&x.disease===cfg.disease&&x.indicator===cfg.indicator&&x.frequency===cfg.frequency);
    if(!item||!item.recommendation_id) throw new Error('该数据集没有可用的 KG 推荐（recommendation）');
    return item;
  }
  async function ensureRun(item){
    const listed=await getJSON('/api/v1/kg/shadow-executions?country='+encodeURIComponent(item.country)+'&disease='+encodeURIComponent(item.disease));
    const existing=(listed.items||[]).find(r=>r.recommendation_id===item.recommendation_id);
    if(existing) return {run_id:existing.run_id,replayed:true};
    const idem='dual-ui-'+item.recommendation_id;
    const d=await reqJSON('/api/v1/kg/shadow-executions',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':idem},body:JSON.stringify({recommendation_id:item.recommendation_id})});
    if(!d.run_id) throw new Error(d.code||d.detail||'Shadow 执行未返回 run_id');
    return {run_id:d.run_id,replayed:d.status==='IDEMPOTENT_REPLAY'};
  }
  function renderProfile(item,fc){
    const hist=fc.history||[];
    const nonzero=hist.filter(h=>Number(h.value_numeric)>0).length;
    $('kgDlProfile').innerHTML=compactInfo([
      ['观测数',`<b>${esc(item.n_obs)}</b> 条（画像）/ ${hist.length} 条（当前序列）`],
      ['时间范围',`${esc(item.first_date)} 至 ${esc(item.last_date)}`],
      ['最新性',esc(fc.freshness==='STALE_HISTORICAL_REPLAY'?'历史数据（较旧）':'当前周度数据')],
      ['非零周期',`${nonzero} 个`],
      ['覆盖率',hist.length?'100%（无缺周/缺月）':'—'],
      ['预测资格等级',`<b>${esc(item.eligibility)}</b>`],
      ['快照 SHA',`<code>${short(item.snapshot_sha256)}</code>`],
      ['画像 SHA',`<code>${short(item.profile_sha256)}</code>`]
    ]);
  }
  function renderRec(item,rec){
    const r=rec.recommendation||{};
    $('kgDlRec').innerHTML=compactInfo([
      ['推荐模型',`<b>${esc(modelZhName[r.adapter_id]||r.adapter_id)}</b><br><code>${esc(r.adapter_id)}</code>`],
      ['推荐理由','数据结构、疾病机制、论文证据、参数来源与执行条件六个维度评分最高（明细见页面上方推荐区块）'],
      ['执行前评分',`${fmt(r.total_score,1)} / 100；参数可信度 ${fmt(r.parameter_confidence_score,1)} / 100`],
      ['重要提示','<b>该分数是证据充分程度，不是预测准确率</b>；是否采用由真实回测决定'],
      ['执行合同',`<code>${short(r.execution_contract_id,18)}</code>（${esc(r.contract_status||'—')}）`]
    ]);
  }
  function renderRun(run,runId,replayed){
    const p=run.profile||{};
    $('kgDlRunInfo').innerHTML=compactInfo([
      ['运行状态',`${esc(run.status)}${replayed?'（幂等复现，未重复执行）':''}`],
      ['run_id',`<code>${esc(runId)}</code>`],
      ['数据集 SHA',`<code>${short(p.dataset_sha256)}</code>`],
      ['适配器 / 版本',`<code>${esc(run.model_name)}</code> / <code>${esc(p.impl_version||'')}</code>`],
      ['随机种子',esc(p.seed)],
      ['训练截止（data_through）',esc(run.data_through)],
      ['标识','<b>Shadow · 未发布 · 未晋级</b>']
    ]);
  }
  function renderBacktest(bt){
    const metrics=(bt.metrics||[]).filter(m=>m.horizon===0);
    const byH={};(bt.metrics||[]).forEach(m=>{if(m.horizon>0){(byH[m.adapter_id]??={})[m.horizon]=m;}});
    const failedFolds=(bt.folds||[]).filter(f=>f.status!=='OK');
    const champion=bt.champion||{};
    const rows=metrics.map(m=>[
      esc(modelZhName[m.adapter_id]||m.adapter_id)+(m.adapter_id===(champion.champion_adapter_id||'')?' 🏆':''),
      fmt(m.mae),fmt(m.rmse),fmt(m.wape),fmt(m.mase),
      m.interval_coverage===null?'—':Math.round(Number(m.interval_coverage)*100)+'%',
      fmt(m.interval_avg_width),esc(m.labeled_row_count),esc(m.missing_label_count)
    ]);
    let html=table(['模型','MAE','RMSE','WAPE','MASE','区间覆盖率','平均区间宽','有效样本','缺失标签'],rows);
    html+=`<p>Champion：<b>${esc(modelZhName[champion.champion_adapter_id]||champion.champion_adapter_id||'—')}</b>（${esc(championZh[champion.champion_source]||champion.champion_source||'—')}）；原因：<code>${esc(champion.champion_reason||'—')}</code>；回测 origin 数：<b>${esc(new Set((bt.folds||[]).map(f=>f.origin_index)).size/(new Set((bt.folds||[]).map(f=>f.adapter_id)).size||1))}</b></p>`;
    const ne=bt.not_eligible||{};
    if(Object.keys(ne).length) html+=`<p>不适用模型：${Object.entries(ne).map(([k,v])=>`${esc(modelZhName[k]||k)}（<code>${esc(v)}</code>）`).join('；')}</p>`;
    if(failedFolds.length) html+=`<details><summary>失败 / 跳过 fold（${failedFolds.length}）</summary>${table(['模型','origin','状态'],failedFolds.map(f=>[esc(f.adapter_id),esc(f.origin_period),esc(f.status)]))}</details>`;
    const hRows=[];
    Object.entries(byH).forEach(([a,hs])=>{[1,2,3,4].forEach(h=>{const m=hs[h];if(m)hRows.push([esc(modelZhName[a]||a),'H'+h,fmt(m.mae),fmt(m.wape),m.interval_coverage===null?'—':Math.round(Number(m.interval_coverage)*100)+'%']);});});
    html+=`<details><summary>分步长（H1–H4）指标</summary>${table(['模型','步长','MAE','WAPE','区间覆盖率'],hRows)}</details>`;
    $('kgDlBacktest').innerHTML=html;
  }
  function drawChart(fc){
    const svg=$('kgDlChart'); svg.innerHTML='';
    const hist=(fc.history||[]).map(h=>({x:h.period_start,y:Number(h.value_numeric)})).filter(p=>Number.isFinite(p.y));
    const fut=(fc.values||[]).filter(v=>v.row_type==='FUTURE').sort((a,b)=>a.horizon-b.horizon);
    if(!hist.length){svg.setAttribute('viewBox','0 0 900 40');return;}
    const W=900,H=300,PL=52,PR=16,PT=16,PB=34;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const ns='http://www.w3.org/2000/svg';
    const points=hist.concat(fut.map(f=>({x:f.target_period_start,y:Number(f.yhat)})));
    const xs=points.map(p=>new Date(p.x).getTime());
    const x0=Math.min(...xs),x1=Math.max(...xs);
    const ymax=Math.max(...points.map(p=>p.y),...fut.map(f=>Number(f.upper_value)||0))*1.1||1;
    const sx=t=>PL+(x1===x0?0:(t-x0)/(x1-x0))*(W-PL-PR);
    const sy=v=>H-PB-(v/ymax)*(H-PT-PB);
    const mk=(tag,attrs)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e;};
    [0,0.25,0.5,0.75,1].forEach(fr=>{const yv=ymax*fr;svg.append(mk('line',{x1:PL,y1:sy(yv),x2:W-PR,y2:sy(yv),class:'kg-chart-grid'}));const t=mk('text',{x:PL-6,y:sy(yv)+4,'text-anchor':'end',class:'kg-chart-label'});t.textContent=String(Math.round(yv));svg.append(t);});
    if(fut.length){
      const upper=fut.map(f=>[sx(new Date(f.target_period_start).getTime()),sy(Number(f.upper_value)||0)]);
      const lower=fut.map(f=>[sx(new Date(f.target_period_start).getTime()),sy(Number(f.lower_value)||0)]).reverse();
      const lastH=hist[hist.length-1];
      const anchor=[[sx(new Date(lastH.x).getTime()),sy(lastH.y)]];
      svg.append(mk('polygon',{points:anchor.concat(upper).concat(lower).concat(anchor).map(p=>p.join(',')).join(' '),class:'kg-chart-band'}));
    }
    const path=hist.map((p,i)=>`${i?'L':'M'}${sx(new Date(p.x).getTime())},${sy(p.y)}`).join(' ');
    svg.append(mk('path',{d:path,class:'kg-chart-history'}));
    if(fut.length){
      const lastH=hist[hist.length-1];
      const fp=[`M${sx(new Date(lastH.x).getTime())},${sy(lastH.y)}`].concat(fut.map(f=>`L${sx(new Date(f.target_period_start).getTime())},${sy(Number(f.yhat)||0)}`)).join(' ');
      svg.append(mk('path',{d:fp,class:'kg-chart-forecast'}));
      fut.forEach(f=>{const t=mk('text',{x:sx(new Date(f.target_period_start).getTime()),y:sy(Number(f.yhat)||0)-8,'text-anchor':'middle',class:'kg-chart-label'});t.textContent='H'+f.horizon;svg.append(t);});
    }
    const lx=mk('text',{x:PL,y:H-8,class:'kg-chart-label'});lx.textContent=new Date(hist[0].x).toISOString().slice(0,10);svg.append(lx);
    const rx=mk('text',{x:W-PR,y:H-8,'text-anchor':'end',class:'kg-chart-label'});rx.textContent=fut.length?fut[fut.length-1].target_period_start:new Date(hist[hist.length-1].x).toISOString().slice(0,10);svg.append(rx);
  }
  function renderForecast(fc){
    drawChart(fc);
    const fut=(fc.values||[]).filter(v=>v.row_type==='FUTURE').sort((a,b)=>a.horizon-b.horizon);
    const rows=fut.map(v=>['H'+v.horizon,esc(v.target_period_start),fmt(v.yhat),fmt(v.lower_value),fmt(v.upper_value),esc((v.properties||{}).feedback_maturity==='AWAITING_ACTUAL'?'等待真实值':'—')]);
    $('kgDlForecast').innerHTML=(rows.length?table(['步长','目标周期','预测值 yhat','下限','上限','反馈状态'],rows):'<p class="kg-empty">无未来预测行</p>')
      +`<p class="kg-plain-note">data_through：<b>${esc(fc.data_through)}</b>；新鲜度：<b>${esc(fc.freshness==='STALE_HISTORICAL_REPLAY'?'历史回放（数据较旧）':'当前 Demo Shadow')}</b>。实线为历史真实值，虚线为 Shadow 预测，阴影为 95% 区间。</p>`;
  }
  function renderFeedback(fb){
    const s=fb.summary||{};
    const cycles=fb.learning_cycles||[];
    let html=compactInfo([
      ['已成熟反馈数',`<b>${esc(s.matured_count??0)}</b>`],
      ['待真实值反馈数',`<b>${esc(s.awaiting_actual_count??0)}</b>`],
      ['平均绝对误差（成熟反馈）',fmt(s.mean_absolute_error)],
      ['区间经验覆盖率',s.interval_empirical_coverage===null||s.interval_empirical_coverage===undefined?'—':Math.round(Number(s.interval_empirical_coverage)*100)+'%'],
      ['最近 learning cycle',cycles.length?`<code>${short(cycles[cycles.length-1].learning_cycle_id,18)}</code>（${esc(cycles[cycles.length-1].cycle_type)}）`:'—']
    ]);
    const af=fb.analyst_feedback||[];
    html+=af.length?table(['时间','评估人','结论','评分','备注'],af.map(x=>[esc(x.created_at),esc(x.actor),esc(x.decision_code),esc(x.rating??'—'),esc(x.comment||'—')])):'<p class="kg-empty">暂无人工反馈</p>';
    $('kgDlFeedback').innerHTML=html;
  }
  async function loadAll(key){
    const cfg=DS[key];
    current={key,runId:null};
    ['kgDlProfile','kgDlRec','kgDlRunInfo','kgDlBacktest','kgDlForecast','kgDlFeedback'].forEach(id=>{$(id).innerHTML='<p class="kg-empty">加载中…</p>';});
    $('kgDlChart').innerHTML='';
    $('kgDlLimits').innerHTML=cfg.limits;
    $('kgDlStatus').textContent='正在执行 / 读取 Shadow 闭环…';
    const item=await findRecommendation(key);
    const run=await ensureRun(item);
    current.runId=run.run_id;
    const [rec,runDetail,bt,fc,fb]=await Promise.all([
      getJSON('/api/v1/kg/recommendations/'+encodeURIComponent(item.recommendation_id)),
      getJSON('/api/v1/kg/shadow-executions/'+encodeURIComponent(run.run_id)),
      getJSON('/api/v1/kg/shadow-executions/'+encodeURIComponent(run.run_id)+'/backtest'),
      getJSON('/api/v1/kg/shadow-executions/'+encodeURIComponent(run.run_id)+'/forecast'),
      getJSON('/api/v1/kg/shadow-executions/'+encodeURIComponent(run.run_id)+'/feedback')
    ]);
    renderProfile(item,fc);
    renderRec(item,rec);
    renderRun(runDetail.run||{},run.run_id,run.replayed);
    renderBacktest(bt);
    renderForecast(fc);
    renderFeedback(fb);
    $('kgDlStatus').innerHTML=`已加载 <b>${esc(key==='AUT'?'奥地利 / 军团菌病':'秘鲁 / 流感')}</b> 的 Shadow 闭环（run_id <code>${esc(run.run_id.slice(0,18))}…</code>）。`;
  }
  $('kgDlRun').addEventListener('click',()=>{
    const key=$('kgDlDataset').value;
    loadAll(key).catch(e=>{$('kgDlStatus').textContent='闭环加载失败：'+e.message;});
  });
  $('kgDlDataset').addEventListener('change',()=>{ if(current&&current.key!==$('kgDlDataset').value){$('kgDlStatus').textContent='数据集已切换，点击按钮加载新数据集（不会沿用上一数据集上下文）。';}});
  $('kgDlFeedbackForm').addEventListener('submit',ev=>{
    ev.preventDefault();
    if(!current||!current.runId){$('kgDlFeedbackMsg').textContent='请先执行 / 加载一个 Shadow 闭环。';return;}
    const fd=new FormData(ev.target);
    const body={actor:String(fd.get('actor')||''),decision_code:String(fd.get('decision_code')||''),comment:String(fd.get('comment')||'')};
    const rating=String(fd.get('rating')||''); if(rating) body.rating=Number(rating);
    reqJSON('/api/v1/kg/shadow-executions/'+encodeURIComponent(current.runId)+'/analyst-feedback',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':'analyst-'+current.runId+'-'+Date.now()},body:JSON.stringify(body)})
      .then(d=>{$('kgDlFeedbackMsg').textContent='人工反馈已记录（feedback_id '+String(d.feedback_id).slice(0,8)+'…）；不会影响病例数据，也不会触发晋级。';return loadAll(current.key);})
      .catch(e=>{$('kgDlFeedbackMsg').textContent='反馈提交失败：'+e.message;});
  });
})();

/* kg-param-loop section: parameter tuning + controlled self-learning demo (kg_pt).
   Guarded by #kg-param-loop presence; failures here never touch other sections. */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const root=$('kg-param-loop'); if(!root) return;
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function reqJSON(url,opts){const r=await fetch(url,{credentials:'same-origin',...(opts||{})}); const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d;}
  const getJSON=url=>reqJSON(url);
  function table(headers,rows){return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function compactInfo(rows){return `<dl class="kg-info-list">${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;}
  function fmt(v,d=3){if(v===null||v===undefined||v==='')return '—'; const n=Number(v); return Number.isFinite(n)?String(Math.round(n*10**d)/10**d):esc(String(v));}
  function pct(v){if(v===null||v===undefined||v==='')return '—'; const n=Number(v); return Number.isFinite(n)?(Math.round(n*1000)/10)+'%':'—';}
  function short(sha,n=12){return sha?esc(String(sha).slice(0,n))+'…':'—';}
  function fail(id,e){const el=$(id); if(el) el.innerHTML=`<p class="kg-error">加载失败：${esc(e.message)}（不影响本页其他区块）</p>`;}
  const DEMO_KEY='AUT:legionella:legionnaires_reported_cases:month';
  const modelZhName={'negbin_dglm.v1':'负二项动态广义线性模型','hurdle_count_ss.v1':'Hurdle 动态计数状态空间模型','last_value':'基线：上一期值','rolling_mean':'基线：滑动均值','drift':'基线：漂移趋势','seasonal_naive':'基线：季节朴素','tuned':'调优后的 KG 模型'};
  const parameterSourceZh={PAPER_EXTRACTED:'论文提取',IMPLEMENTATION_DEFAULT:'实现默认值',DATA_PROFILE_RULE:'数据画像规则',OPTIMIZER_SELECTED:'优化器选定'};
  const championSourceZh={KG_RECOMMENDED_MODEL:'KG 推荐模型',EMPIRICAL_BASELINE_OVERRIDE:'经验基线胜出（校正 KG 推荐）'};
  const cycleTypeZh={PARAMETER_TUNING_EVALUATION:'参数调优评估（第 1 周期）',CONTROLLED_RETRAIN_HISTORICAL_REPLAY:'受控重训 · 历史回放（第 2 周期）'};
  const kgModelStatusZh={EXECUTED_CHAMPION:'已执行并成为 Champion',EXECUTED_NOT_CHAMPION:'已执行但未成为 Champion',EXECUTED_NO_COMMON_COMPARISON:'已执行但无共同比较样本'};
  const reasonZh={
    TUNED_KG_MODEL_COMMON_WAPE_IMPROVEMENT_GE_1PCT:'调优后 KG 模型在共同样本 WAPE 上相对最佳基线改善 ≥1%，KG 推荐模型成为 Champion',
    BASELINE_BETTER_OR_IMPROVEMENT_BELOW_1PCT:'最佳基线更优，或调优模型改善不足 1%，Champion 由经验基线接任（校正 KG 推荐）',
    NO_BASELINE_COMMON_SAMPLE:'没有可与基线比较的共同样本',
    NO_COMMON_SAMPLE_AT_ALL:'完全缺少可比较的共同样本',
    INCUMBENT_RETAINED_RETRAIN_NOT_TRIGGERED:'未达到重训触发条件，现任 Champion 留任',
    INCUMBENT_RETAINED_CHALLENGER_FAILED_ALL_TRIALS:'挑战者全部 trial 失败，现任 Champion 留任',
    CHALLENGER_COMMON_WAPE_IMPROVEMENT_GE_1PCT:'挑战者在共同样本 WAPE 上改善 ≥1%，接任 Champion',
    INCUMBENT_RETAINED_CHALLENGER_NOT_SIGNIFICANTLY_BETTER:'挑战者未显著优于现任 Champion，现任留任'
  };
  const ns='http://www.w3.org/2000/svg';
  const mk=(tag,attrs)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e;};
  let current={shadowRunId:null,tuningRunId:null};

  function renderContext(d){
    const s=d.snapshot||{}, rec=d.recommendation||{};
    $('kgPtContext').innerHTML=compactInfo([
      ['国家',esc(rec.country||'AUT')+'（奥地利）'],
      ['病种',esc(rec.disease||'legionella')+'（军团菌病）'],
      ['指标',esc(rec.indicator||'legionnaires_reported_cases')+'（军团菌病报告病例数）'],
      ['频率',esc(rec.frequency||'month')+'（每月）'],
      ['冻结观测数',`<b>${esc(s.n_obs??'—')}</b> 条（${esc(s.first_period||'—')} 至 ${esc(s.last_period||'—')}）`],
      ['缺失值语义','保持 NULL，从不填充（<code>'+esc(s.missing_semantics||'—')+'</code>）'],
      ['快照 SHA','<code>'+short(s.snapshot_sha256)+'</code>']
    ]);
  }
  function renderRec(d){
    const rec=d.recommendation;
    const reasons=d.recommendation_reason_zh||[];
    const comps=d.recommendation_components||[];
    let html=rec?compactInfo([
      ['推荐模型',`<b>${esc(modelZhName[rec.adapter_id]||rec.adapter_id)}</b><br><code>${esc(rec.adapter_id)}</code>`],
      ['执行前证据总分',`${fmt(rec.total_score,1)} / 100`],
      ['参数可信度',`${fmt(rec.parameter_confidence_score,1)} / 100`],
      ['重要提示','<b>以上分数代表证据充分程度，不是预测准确率</b>；是否采用由独立测试与 Champion 决策决定']
    ]):'<p class="kg-empty">未找到 KG 推荐记录。</p>';
    if(reasons.length) html+='<ul>'+reasons.map(r=>`<li>${esc(r)}</li>`).join('')+'</ul>';
    if(comps.length){
      const byDim={}; comps.forEach(c=>{(byDim[c.dimension]??=[]).push(c);});
      const dimRows=Object.entries(byDim).map(([dim,items])=>{
        const score=items.reduce((n,x)=>n+Number(x.score||0),0), max=items.reduce((n,x)=>n+Number(x.max_score||0),0);
        return [esc(dim),dim==='PENALTY'?`扣 ${Math.abs(score)} 分`:`${fmt(score,1)} / ${fmt(max,1)}`,esc(String(items.length))];
      });
      html+=`<details class="kg-audit-details"><summary>查看六维评分明细（${comps.length} 条）</summary>${table(['评分维度','得分','条目数'],dimRows)}</details>`;
    }
    $('kgPtRec').innerHTML=html;
  }
  function drawTimeline(d){
    const svg=$('kgPtTimeline'); svg.innerHTML='';
    const sp=d.split;
    if(!sp){svg.setAttribute('viewBox','0 0 900 40'); $('kgPtTimelineLegend').innerHTML='<p class="kg-empty">暂无切分信息</p>'; return;}
    const W=900,H=104,PL=16,PR=16;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const t0=new Date(sp.train_start).getTime(), t1=new Date(sp.test_end).getTime();
    const sx=t=>PL+(t1===t0?0:(t-t0)/(t1-t0))*(W-PL-PR);
    const segs=[
      {a:sp.train_start,b:sp.train_end,cls:'kg-pt-band-train',label:`训练 ${sp.train_rows} 条`},
      {a:sp.validation_start,b:sp.validation_end,cls:'kg-pt-band-val',label:`调参验证 ${sp.validation_rows} 条`},
      {a:sp.test_start,b:sp.test_end,cls:'kg-pt-band-test',label:`独立测试 ${sp.test_rows} 条`}
    ];
    segs.forEach(sg=>{
      const x1=sx(new Date(sg.a).getTime()), x2=Math.max(x1+2,sx(new Date(sg.b).getTime()));
      svg.append(mk('rect',{x:x1,y:26,width:x2-x1,height:34,rx:6,class:sg.cls}));
      const t=mk('text',{x:(x1+x2)/2,y:47,'text-anchor':'middle',class:'kg-pt-band-label'}); t.textContent=sg.label; svg.append(t);
      const tl=mk('text',{x:x1,y:76,'text-anchor':'middle',class:'kg-chart-label'}); tl.textContent=String(sg.a).slice(0,7); svg.append(tl);
    });
    const tr=mk('text',{x:sx(t1),y:76,'text-anchor':'end',class:'kg-chart-label'}); tr.textContent=String(sp.test_end).slice(0,7); svg.append(tr);
    const note=mk('text',{x:PL,y:96,class:'kg-chart-label'}); note.textContent='时间轴按日历日期等比绘制；三段首尾相接、互不重叠，调参与测试永远不接触未来数据。'; svg.append(note);
    $('kgPtTimelineLegend').innerHTML='<span class="kg-pt-chip train">训练</span><span class="kg-pt-chip val">调参验证</span><span class="kg-pt-chip test">独立测试</span>';
  }
  function renderParams(d,trialsData){
    const contracts=d.parameter_contracts||[];
    const vm=d.validation_metrics||{};
    const ev1=((d.learning_cycles||[])[0]||{}).evidence||{};
    const bestParams=(vm.best_trial&&vm.best_trial.params)||ev1.best_params||{};
    const bestTrialId=(vm.best_trial||{}).trial_id||'';
    const bestSources={};
    ((trialsData||{}).trials||[]).forEach(t=>{ if(t.trial_id===bestTrialId&&t.parameter_source) Object.assign(bestSources,t.parameter_source); });
    if(!contracts.length){$('kgPtParams').innerHTML='<p class="kg-empty">暂无参数合同记录</p>'; return;}
    const rows=contracts.map(c=>{
      const name=c.parameter_name;
      const best=bestParams[name];
      const range=(c.lower_bound!==null&&c.lower_bound!==undefined)?`${fmt(c.lower_bound,4)} ~ ${fmt(c.upper_bound,4)}`:'—';
      const src=bestSources[name]||c.parameter_source;
      const srcZh=parameterSourceZh[src]||src||'—';
      return [
        `<code>${esc(name)}</code>`,
        fmt(c.default_value,4),
        esc(range),
        best===undefined||best===null?'—':`<b>${fmt(best,6)}</b>`,
        esc(srcZh)+`（<code>${esc(src||'—')}</code>）`,
        c.tunable?'可调':'固定'
      ];
    });
    let html=table(['参数名','原默认值','搜索范围','最优值','来源','可调性'],rows);
    html+='<p class="kg-plain-note">“最优值”来自调参验证窗口上共同样本 WAPE 最小的 trial（来源标注为优化器选定）；调参过程从未接触独立测试数据。</p>';
    const detailRows=contracts.map(c=>[`<code>${esc(c.parameter_name)}</code>`,esc(c.transform||'none'),esc(c.missing_reason||'—'),esc(c.safety_constraint||'—')]);
    html+=`<details class="kg-audit-details"><summary>查看变换、缺失原因与安全约束</summary>${table(['参数名','采样变换','证据缺失原因','安全约束'],detailRows)}</details>`;
    $('kgPtParams').innerHTML=html;
  }
  function drawTuning(trialsData){
    const svg=$('kgPtTuning'); svg.innerHTML='';
    const trials=((trialsData||{}).trials||[]);
    const ok=trials.filter(t=>t.common_wape!==null&&t.common_wape!==undefined&&Number.isFinite(Number(t.common_wape)));
    if(!ok.length){svg.setAttribute('viewBox','0 0 900 40'); $('kgPtTuningNote').innerHTML='<p class="kg-empty">暂无可绘制的 trial</p>'; return;}
    const W=900,H=260,PL=52,PR=16,PT=16,PB=34;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const xs=ok.map(t=>t.trial_index), ys=ok.map(t=>Number(t.common_wape));
    const x0=Math.min(...xs),x1=Math.max(...xs);
    let y0=Math.min(...ys),y1=Math.max(...ys); const pad=(y1-y0||0.01)*0.15; y0=Math.max(0,y0-pad); y1=y1+pad;
    const sx=v=>PL+(x1===x0?0:(v-x0)/(x1-x0))*(W-PL-PR);
    const sy=v=>H-PB-((v-y0)/(y1-y0))*(H-PT-PB);
    [0,0.25,0.5,0.75,1].forEach(fr=>{const yv=y0+(y1-y0)*fr; svg.append(mk('line',{x1:PL,y1:sy(yv),x2:W-PR,y2:sy(yv),class:'kg-chart-grid'})); const t=mk('text',{x:PL-6,y:sy(yv)+4,'text-anchor':'end',class:'kg-chart-label'}); t.textContent=String(Math.round(yv*1000)/1000); svg.append(t);});
    const path=ok.map((t,i)=>`${i?'L':'M'}${sx(t.trial_index)},${sy(Number(t.common_wape))}`).join(' ');
    svg.append(mk('path',{d:path,class:'kg-chart-history'}));
    const best=ok.reduce((a,b)=>Number(a.common_wape)<=Number(b.common_wape)?a:b);
    ok.forEach(t=>{
      const failed=t.gate_pass===false||t.failure_reason;
      svg.append(mk('circle',{cx:sx(t.trial_index),cy:sy(Number(t.common_wape)),r:t.trial_index===best.trial_index?7:4,class:failed?'kg-pt-dot-failed':(t.trial_index===best.trial_index?'kg-pt-dot-best':'kg-pt-dot')}));
    });
    const bt=mk('text',{x:sx(best.trial_index),y:sy(Number(best.common_wape))-12,'text-anchor':'middle',class:'kg-pt-best-label'}); bt.textContent=`最优 trial ${best.trial_index}`; svg.append(bt);
    const xl=mk('text',{x:PL,y:H-8,class:'kg-chart-label'}); xl.textContent=`trial ${x0}（默认值）`; svg.append(xl);
    const xr=mk('text',{x:W-PR,y:H-8,'text-anchor':'end',class:'kg-chart-label'}); xr.textContent=`trial ${x1}`; svg.append(xr);
    const failedCount=trials.filter(t=>t.gate_pass===false||t.failure_reason).length;
    $('kgPtTuningNote').innerHTML=`<p class="kg-plain-note">纵轴为共同样本 WAPE（越低越好）；绿色为最优 trial，红色为门禁未通过或失败的 trial（${failedCount} 个）。trial 0 为调参前的默认参数。</p>`;
  }
  function renderMetrics(d){
    const vm=d.validation_metrics||{};
    const t0=vm.trial0, bt=vm.best_trial;
    let html='';
    if(t0&&bt){
      html+=table(['验证窗口指标（共同样本）','调参前（trial 0 默认值）','调参后（最优 trial）'],[
        ['共同样本 WAPE',fmt(t0.common_wape),`<b>${fmt(bt.common_wape)}</b>`],
        ['区间覆盖率',pct(t0.coverage),pct(bt.coverage)],
        ['平均区间宽度',fmt(t0.avg_interval_width),fmt(bt.avg_interval_width)],
        ['目标函数值',fmt(t0.objective_value,6),fmt(bt.objective_value,6)]
      ]);
    }else{
      html+='<p class="kg-empty">暂无调参前后验证指标</p>';
    }
    const evals=d.test_evaluations||[];
    const cmp=d.test_comparison||{};
    if(evals.length){
      const rows=evals.map(e=>[
        esc(modelZhName[e.adapter_id]||e.adapter_id)+(e.is_tuned_model?'（调优后）':''),
        fmt(e.mae),fmt(e.rmse),fmt(e.wape),fmt(e.smape),fmt(e.mase),
        pct(e.coverage),fmt(e.avg_interval_width),e.agreement_pct===null||e.agreement_pct===undefined?'—':fmt(e.agreement_pct,1)+'%'
      ]);
      html+=`<h3>独立测试窗口指标（全部模型，仅评估一次）</h3>${table(['模型','MAE','RMSE','WAPE','sMAPE','MASE','区间覆盖率','平均区间宽','吻合度'],rows)}`;
      html+=`<p>相对最佳基线提升率：<b>${cmp.improvement_vs_best_baseline===null||cmp.improvement_vs_best_baseline===undefined?'—':(Math.round(Number(cmp.improvement_vs_best_baseline)*10000)/100)+'%'}</b>（最佳基线：${esc(modelZhName[cmp.best_baseline_adapter]||cmp.best_baseline_adapter||'—')}，WAPE ${fmt(cmp.best_baseline_wape)}）</p>`;
    }
    $('kgPtMetrics').innerHTML=html;
  }
  function renderTest(d){
    const evals=d.test_evaluations||[];
    const tuned=evals.find(e=>e.is_tuned_model);
    if(!tuned){$('kgPtTest').innerHTML='<p class="kg-empty">暂无独立测试评估</p>'; return;}
    const agree=tuned.agreement_pct===null||tuned.agreement_pct===undefined?'—':fmt(tuned.agreement_pct,1)+'%';
    $('kgPtTest').innerHTML=
      `<div class="kg-pt-agreement">预测吻合度 ${esc(agree)}</div>`
      +`<p class="kg-pt-disclaimer">该数值是基于WAPE换算的易读指标，不是分类准确率，也不代表未来保证准确。</p>`
      +table(['指标','数值'],[
        ['MAE',fmt(tuned.mae)],['RMSE',fmt(tuned.rmse)],['WAPE',fmt(tuned.wape)],['sMAPE',fmt(tuned.smape)],
        ['MASE',fmt(tuned.mase)],['区间覆盖率',pct(tuned.coverage)],['平均区间宽度',fmt(tuned.avg_interval_width)]
      ])
      +`<details class="kg-audit-details"><summary>查看评估审计标识</summary><code>${esc(tuned.eval_id||'—')}</code></details>`;
  }
  function drawForecast(fc){
    const svg=$('kgPtForecast'); svg.innerHTML='';
    const hist=(fc.history||[]).map(h=>({x:h.period_start,y:h.value_numeric===null||h.value_numeric===undefined?null:Number(h.value_numeric)})).filter(p=>p.y!==null&&Number.isFinite(p.y));
    const values=fc.values||[];
    const testCells=values.filter(v=>v.row_type==='TEST_REPLAY'&&v.yhat!==null&&v.yhat!==undefined);
    const fut=values.filter(v=>v.row_type==='FUTURE').sort((a,b)=>a.horizon-b.horizon);
    if(!hist.length){svg.setAttribute('viewBox','0 0 900 40'); $('kgPtForecastNote').innerHTML='<p class="kg-empty">暂无冻结历史序列</p>'; return;}
    const W=900,H=300,PL=52,PR=16,PT=16,PB=34;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const tail=hist.slice(-72);
    const allX=tail.map(p=>new Date(p.x).getTime()).concat(fut.map(f=>new Date(f.target_period_start).getTime()),testCells.map(c=>new Date(c.target_period_start).getTime()));
    const x0=Math.min(...allX),x1=Math.max(...allX);
    const ymax=Math.max(...tail.map(p=>p.y),...testCells.map(c=>Number(c.upper_value)||0),...testCells.map(c=>Number(c.yhat)||0),...fut.map(f=>Number(f.upper_value)||0))*1.1||1;
    const sx=t=>PL+(x1===x0?0:(t-x0)/(x1-x0))*(W-PL-PR);
    const sy=v=>H-PB-(v/ymax)*(H-PT-PB);
    [0,0.25,0.5,0.75,1].forEach(fr=>{const yv=ymax*fr; svg.append(mk('line',{x1:PL,y1:sy(yv),x2:W-PR,y2:sy(yv),class:'kg-chart-grid'})); const t=mk('text',{x:PL-6,y:sy(yv)+4,'text-anchor':'end',class:'kg-chart-label'}); t.textContent=String(Math.round(yv)); svg.append(t);});
    const path=tail.map((p,i)=>`${i?'L':'M'}${sx(new Date(p.x).getTime())},${sy(p.y)}`).join(' ');
    svg.append(mk('path',{d:path,class:'kg-chart-history'}));
    testCells.forEach(c=>{
      const cx=sx(new Date(c.target_period_start).getTime());
      if(c.lower_value!==null&&c.upper_value!==null&&c.lower_value!==undefined&&c.upper_value!==undefined)
        svg.append(mk('line',{x1:cx,y1:sy(Number(c.lower_value)),x2:cx,y2:sy(Number(c.upper_value)),class:'kg-pt-test-interval'}));
      svg.append(mk('circle',{cx,cy:sy(Number(c.yhat)),r:3.5,class:'kg-pt-test-dot'}));
    });
    if(fut.length){
      const upper=fut.map(f=>[sx(new Date(f.target_period_start).getTime()),sy(Number(f.upper_value)||0)]);
      const lower=fut.map(f=>[sx(new Date(f.target_period_start).getTime()),sy(Number(f.lower_value)||0)]).reverse();
      const anchor=[[sx(new Date(hist[hist.length-1].x).getTime()),sy(hist[hist.length-1].y)]];
      svg.append(mk('polygon',{points:anchor.concat(upper).concat(lower).concat(anchor).map(p=>p.join(',')).join(' '),class:'kg-chart-band'}));
      const fp=[`M${anchor[0][0]},${anchor[0][1]}`].concat(fut.map(f=>`L${sx(new Date(f.target_period_start).getTime())},${sy(Number(f.yhat)||0)}`)).join(' ');
      svg.append(mk('path',{d:fp,class:'kg-chart-forecast'}));
      fut.forEach(f=>{const t=mk('text',{x:sx(new Date(f.target_period_start).getTime()),y:sy(Number(f.yhat)||0)-8,'text-anchor':'middle',class:'kg-chart-label'}); t.textContent='H'+f.horizon; svg.append(t);});
    }
    const lx=mk('text',{x:PL,y:H-8,class:'kg-chart-label'}); lx.textContent=String(tail[0].x).slice(0,10); svg.append(lx);
    const rx=mk('text',{x:W-PR,y:H-8,'text-anchor':'end',class:'kg-chart-label'}); rx.textContent=fut.length?String(fut[fut.length-1].target_period_start).slice(0,10):String(tail[tail.length-1].x).slice(0,10); svg.append(rx);
    const rows=fut.map(f=>['H'+f.horizon,esc(f.target_period_start),fmt(f.yhat),fmt(f.lower_value),fmt(f.upper_value),'等待真实值']);
    $('kgPtForecastNote').innerHTML=(rows.length?table(['步长','目标周期','预测值 yhat','下限','上限','反馈状态'],rows):'<p class="kg-empty">无未来预测行</p>')
      +'<p class="kg-plain-note">实线为冻结快照中的历史实际值（近 72 期），橙色点为独立测试窗口预测（竖线为 95% 区间），虚线与阴影为面向未来的 H1–H4 预测及区间带（真实值未知，等待反馈成熟）。</p>';
  }
  function renderChampion(d){
    const decisions=d.champion_decisions||[];
    if(!decisions.length){$('kgPtChampion').innerHTML='<p class="kg-empty">暂无 Champion 决策</p>'; return;}
    const last=decisions[decisions.length-1];
    $('kgPtChampion').innerHTML=compactInfo([
      ['当前 Champion',`<b>${esc(modelZhName[last.champion_adapter_id]||last.champion_adapter_id||'—')}</b><br><code>${esc(last.champion_adapter_id||'—')}</code>`],
      ['来源',esc(championSourceZh[last.champion_source]||last.champion_source||'—')],
      ['KG 模型状态',esc(kgModelStatusZh[last.kg_model_status]||last.kg_model_status||'—')],
      ['胜负原因',esc(reasonZh[last.reason]||last.reason||'—')],
      ['是否已晋级',`<b>${last.promotion_applied?'是':'否'}</b>（Shadow 决策，永不自动晋级）`]
    ])+`<details class="kg-audit-details"><summary>查看原始原因代码</summary><code>${esc(last.reason||'—')}</code></details>`;
  }
  function renderFeedback(d){
    const s=d.feedback_summary||{};
    const m=Number(s.matured_count||0), a=Number(s.awaiting_count||0), sup=Number(s.superseded_count||0);
    const total=m+a+sup||1;
    const W=300,H=14;
    const wM=W*m/total, wA=W*a/total, wS=W*sup/total;
    // SVG attribute widths (not inline CSS) so the bar complies with style-src 'self'.
    const bar=`<svg class="kg-pt-progress" viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="反馈成熟进度">`
      +`<rect class="matured" x="0" y="0" width="${wM}" height="${H}"></rect>`
      +`<rect class="awaiting" x="${wM}" y="0" width="${wA}" height="${H}"></rect>`
      +`<rect class="superseded" x="${wM+wA}" y="0" width="${wS}" height="${H}"></rect></svg>`;
    $('kgPtFeedback').innerHTML=bar+compactInfo([
      ['已成熟（已有真实值核验）',`<b>${m}</b> 条`],
      ['等待真实值',`<b>${a}</b> 条`],
      ['已被回放核验行替代',`<b>${sup}</b> 条`],
      ['成熟反馈平均绝对误差',fmt(s.mean_abs_error)],
      ['区间经验覆盖率',s.empirical_coverage===null||s.empirical_coverage===undefined?'—':Math.round(Number(s.empirical_coverage)*100)+'%']
    ])+'<p class="kg-plain-note">绿色=已成熟，橙色=等待真实值，灰色=已被替代。成熟行标注为历史回放（DEMO_HISTORICAL_REPLAY）。</p>';
  }
  function renderCycles(d){
    const cycles=d.learning_cycles||[];
    if(!cycles.length){$('kgPtCycles').innerHTML='<p class="kg-empty">暂无学习闭环记录</p>'; return;}
    const decisions=d.champion_decisions||[];
    const byCycle={}; decisions.forEach(dc=>{byCycle[dc.learning_cycle_id]=dc;});
    $('kgPtCycles').innerHTML='<ul class="kg-pt-timeline-list">'+cycles.map(c=>{
      const dc=byCycle[c.learning_cycle_id]||{};
      const ev=c.evidence||{};
      return `<li><b>${esc(cycleTypeZh[c.cycle_type]||c.cycle_type||'—')}</b>`
        +`<br>时间：${esc(c.created_at||'—')}；状态：${esc(c.status||'—')}；成熟反馈 ${esc(c.matured_feedback_count??'—')} / 门槛 ${esc(c.min_matured_feedback??'—')}`
        +(dc.champion_adapter_id?`<br>决策：${esc(modelZhName[dc.champion_adapter_id]||dc.champion_adapter_id)}（${esc(championSourceZh[dc.champion_source]||dc.champion_source||'—')}）；promotion_applied=<b>${dc.promotion_applied?'true':'false'}</b>`:'')
        +(ev.heldout?`<br>留存集状态：<code>${esc(ev.heldout)}</code>（第 2 周期后无剩余独立测试集）`:'')
        +`<br><code>${esc(c.learning_cycle_id||'')}</code></li>`;
    }).join('')+'</ul>';
  }
  function renderLimits(d){
    const limits=d.limitations||[];
    $('kgPtLimits').innerHTML=limits.length?'<ul>'+limits.map(l=>`<li>${esc(l)}</li>`).join('')+'</ul>':'<p class="kg-empty">—</p>';
  }
  function renderNotRun(msg){
    ['kgPtContext','kgPtRec','kgPtParams','kgPtMetrics','kgPtTest','kgPtChampion','kgPtFeedback','kgPtCycles','kgPtLimits','kgPtTimelineLegend','kgPtTuningNote','kgPtForecastNote'].forEach(id=>{const el=$(id); if(el) el.innerHTML=`<p class="kg-empty">${esc(msg)}</p>`;});
    ['kgPtTimeline','kgPtTuning','kgPtForecast'].forEach(id=>{const el=$(id); if(el){el.innerHTML=''; el.setAttribute('viewBox','0 0 900 40');}});
  }
  async function loadAll(){
    let d;
    try{
      d=await getJSON('/api/v1/kg/pt/demo/overview?demo_key='+encodeURIComponent(DEMO_KEY));
    }catch(e){
      if(String(e.message).includes('PT_DEMO_NOT_FOUND')){
        renderNotRun('尚未运行：点击下方“运行 / 刷新”按钮执行参数调优闭环（首次执行约需数十秒，重复执行幂等）。');
        $('kgPtRunInfo').textContent='演示尚未运行。';
        return;
      }
      throw e;
    }
    const runs=d.tuning_runs||[];
    current.tuningRunId=runs.length?runs[0].tuning_run_id:null;
    current.shadowRunId=runs.length?runs[0].shadow_run_id:null;
    const tasks=[
      ['kgPtContext',()=>renderContext(d)],
      ['kgPtRec',()=>renderRec(d)],
      ['kgPtTimeline',()=>drawTimeline(d)],
      ['kgPtMetrics',()=>renderMetrics(d)],
      ['kgPtTest',()=>renderTest(d)],
      ['kgPtChampion',()=>renderChampion(d)],
      ['kgPtFeedback',()=>renderFeedback(d)],
      ['kgPtCycles',()=>renderCycles(d)],
      ['kgPtLimits',()=>renderLimits(d)]
    ];
    let trialsData=null;
    if(current.tuningRunId){
      try{ trialsData=await getJSON('/api/v1/kg/pt/demo/trials?tuning_run_id='+encodeURIComponent(current.tuningRunId)); }
      catch(e){ fail('kgPtTuningNote',e); }
    }
    tasks.push(['kgPtParams',()=>renderParams(d,trialsData)]);
    tasks.push(['kgPtTuning',()=>drawTuning(trialsData)]);
    if(current.shadowRunId){
      try{
        const fc=await getJSON('/api/v1/kg/pt/demo/forecast?run_id='+encodeURIComponent(current.shadowRunId));
        tasks.push(['kgPtForecast',()=>drawForecast(fc)]);
      }catch(e){ fail('kgPtForecastNote',e); }
    }else{
      $('kgPtForecastNote').innerHTML='<p class="kg-empty">暂无 Shadow 运行，无法绘制预测图</p>';
    }
    tasks.forEach(([id,fn])=>{ try{ fn(); }catch(e){ fail(id,e); } });
    $('kgPtRunInfo').innerHTML=`已加载参数优化闭环（调参运行 <code>${short(current.tuningRunId,18)}</code>，Shadow 运行 <code>${short(current.shadowRunId,18)}</code>）。`;
  }
  $('kgPtRun').addEventListener('click',()=>{
    const btn=$('kgPtRun'); btn.disabled=true;
    $('kgPtRunInfo').textContent='正在执行参数调优闭环（Shadow，不写入任何正式数据）…';
    reqJSON('/api/v1/kg/pt/demo/run',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':'pt-ui-'+DEMO_KEY},body:JSON.stringify({demo_key:DEMO_KEY})})
      .then(d=>{
        const replayed=d.status==='IDEMPOTENT_REPLAY'||d.idempotent_replay;
        $('kgPtRunInfo').textContent=replayed
          ?'幂等回放（Idempotent-Replay）：服务端已有相同 Idempotency-Key 的完成结果，本次未重复执行。'
          :`执行完成：Champion=${d.champion?(d.champion.champion_adapter_id||'—'):'—'}；正在重新加载面板…`;
        return loadAll();
      })
      .catch(e=>{$('kgPtRunInfo').textContent='执行失败：'+e.message;})
      .finally(()=>{btn.disabled=false;});
  });
  function start(){ loadAll().catch(e=>{ $('kgPtRunInfo').textContent='参数优化闭环加载失败：'+e.message+'（不影响本页其他区块）'; renderNotRun('加载失败：'+e.message); }); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();

/* kg-qwen-loop section: Qwen autotuning + controlled self-learning demo (kg_qwen).
   Guarded by #kg-qwen-loop presence; failures here never touch other sections.
   渲染字段说明：overview JSON 路径以注释标注；后端未提供的字段一律显示“—”（待后端对齐），
   绝不显示 null/undefined 字面量。 */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const root=$('kg-qwen-loop'); if(!root) return;
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function reqJSON(url,opts){const r=await fetch(url,{credentials:'same-origin',...(opts||{})}); const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d;}
  const getJSON=url=>reqJSON(url);
  function table(headers,rows){return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function compactInfo(rows){return `<dl class="kg-info-list">${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`;}
  function fmt(v,d=3){if(v===null||v===undefined||v==='')return '—'; const n=Number(v); return Number.isFinite(n)?String(Math.round(n*10**d)/10**d):esc(String(v));}
  function pct(v){if(v===null||v===undefined||v==='')return '—'; const n=Number(v); return Number.isFinite(n)?(Math.round(n*1000)/10)+'%':'—';}
  function short(sha,n=12){return sha?esc(String(sha).slice(0,n))+'…':'—';}
  function fail(id,e){const el=$(id); if(el) el.innerHTML=`<p class="kg-error">加载失败：${esc(e.message)}（不影响本页其他区块）</p>`;}
  const WAPE_NOTE='预测吻合度为 WAPE 换算指标（max(0, 1−WAPE)×100），不是分类准确率，也不保证未来准确。';
  const TARGET_AGREEMENT=70;
  const TRACKS={
    A:{key:'AUT:legionella:legionnaires_reported_cases:month',
       runIdemKey:'kq-trackA-qwen3-20260812',
       badge:'轨道 A：奥地利 / 军团菌病 / 月频',
       identity:'REUSED_HISTORICAL_BENCHMARK / NOT_FRESH_HELDOUT',
       note:'历史回放演示轨道：测试窗已在以往调参中被查看，其指标仅为机制演示，不得称为前瞻吻合度。'},
    B:{key:'FIN:hantavirus:puumala_virus_reports:week',
       runIdemKey:'kq-trackB-qwen2-20260812',
       badge:'轨道 B：芬兰 / 汉坦病毒病 / 周频',
       identity:'RETROSPECTIVE_REPLAY_NOT_FRESH',
       note:'历史重复评估轨道：该 52 周窗口曾被 3 个 run 重复打开，治理修复后已归类为 RETROSPECTIVE_REPLAY_NOT_FRESH；其 71.97% 吻合度是历史重复评估结果，不构成独立盲测认证，窗口已被全局注册表锁定为 REPLAY_ONLY。'},
    C:{key:'CHL:influenza:influenza_positive_detections:week',
       runIdemKey:'kq-trackC-qwen1-20260812',
       badge:'轨道 C：智利 / 流感 / 周频',
       identity:'FRESH_HELDOUT_LOCKED',
       note:'新鲜盲测轨道：CHL 流感阳性检出序列（FluNet 归一化层，1580 周）此前从未进入任何训练快照、预测运行或调参；取尾部 445 周，最后 52 周窗口在任何 Qwen 调用与调参前由全局注册表原子锁定，跨所有 run 最多消费一次；评估后无论成败窗口即 CONSUMED。'},
    D:{key:'MYS:influenza:influenza_positive_detections:week',
       runIdemKey:'kq-trackD-qwen1-20260813',
       badge:'轨道 D：马来西亚 / 流感 / 周频',
       identity:'FRESH_HELDOUT_LOCKED',
       note:'第三条流感候选链：国家筛选只使用训练/验证段，最后 52 周从未参与候选比较；运行时先由全局注册表锁定，再由 Qwen 在白名单内给出模型与参数方案。是否达到 70% 以一次性盲测结果为准。'}
  };
  const modelZhName={'negbin_dglm.v1':'负二项动态广义线性模型','seasonal_negbin_glm.v1':'季节负二项 GLM','fourier_negbin_glm.v1':'Fourier 负二项 GLM','lagged_count_boosting.v1':'滞后计数梯度提升','seasonal_ensemble.v1':'季节集成模型','last_value':'基线：上一期值','rolling_mean':'基线：滑动均值','drift':'基线：漂移趋势','seasonal_naive':'基线：季节朴素'};
  const countryZh={AUT:'奥地利',FIN:'芬兰',DEU:'德国',CHL:'智利',MYS:'马来西亚'};
  const planSourceZh={QWEN:'Qwen 生成',QWEN_FALLBACK_DETERMINISTIC:'确定性回退（Qwen 输出不可用或未通过校验）'};
  const parameterSourceZh={IMPLEMENTATION_DEFAULT:'实现默认值',DATA_PROFILE_RULE:'数据画像规则',OPTIMIZER_SELECTED:'优化器选择'};
  const evalIdentityZh={REUSED_HISTORICAL_BENCHMARK:'历史回放基准（复用既有测试窗）',NOT_FRESH_HELDOUT:'非新鲜留存（测试窗此前已被查看）',FRESH_HELDOUT_LOCKED:'新鲜独立留存（调参前已锁定，全局仅消费一次）',RETROSPECTIVE_REPLAY_NOT_FRESH:'历史重复评估（同一窗口曾被多个 run 打开），不构成独立盲测认证'};
  const registryStatusZh={REGISTERED:'已注册（未锁定）',LOCKED_UNOPENED:'已锁定未打开（调参前冻结）',CONSUMED:'已消费（全局仅一次）',INVALIDATED:'已作废',REPLAY_ONLY:'仅可回放（永不可作为新鲜盲测）'};
  const certVerdictZh={FRESH_HELDOUT_CERTIFIED:'新鲜盲测认证通过（全部门禁满足）',FRESH_HELDOUT_GATES_FAILED:'新鲜盲测门禁未全部通过（如实记录，不宣称认证）'};
  const validationStatusZh={ACCEPTED:'已通过校验',REJECTED:'已被校验器拒绝',FALLBACK_AFTER_REJECTION:'被拒后回退到确定性方案'};
  const championSourceZh={TUNED_MODEL:'调优模型（Qwen 方案产物）',BASELINE:'基线模型（调优未显著优于基线）'};
  const championReasonZh={
    TUNED_MODEL_COMMON_WAPE_IMPROVEMENT_GE_1PCT:'调优模型在共同样本 WAPE 上相对最佳基线改善 ≥1%，成为 Champion',
    BASELINE_KEPT_IMPROVEMENT_BELOW_1PCT:'调优模型改善不足 1% 或基线更优，Champion 保持为基线（如实记录 NOT_CHAMPION）'
  };
  const targetStatusZh={TARGET_MET:'达成目标（验证 WAPE ≤ 0.30 且验证吻合度 ≥ 70%）',AUTOTUNING_TARGET_NOT_MET_WITHIN_BUDGET:'预算内未达标（如实记录，不宣称准确）'};
  const stopReasonZh={TARGET_MET:'达到优化目标后停止',BUDGET_EXHAUSTED:'预算耗尽停止'};
  const cycleTypeZh={AUTOTUNING_EVALUATION:'Qwen 自动调优评估',FEEDBACK_MATURATION:'反馈成熟核验（历史回放）'};
  const ns='http://www.w3.org/2000/svg';
  const mk=(tag,attrs)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e;};
  let currentTrack='A';

  /* 1. 当前轨道卡片：overview.track.{country,disease,indicator,frequency,identity,is_fresh}（待后端对齐） */
  function renderContext(d){
    const t=d.track||{};
    $('kgQContext').innerHTML=compactInfo([
      ['国家',esc(countryZh[t.country]||t.country||'—')+(t.country?`（${esc(t.country)}）`:'')],
      ['病种',esc(t.disease||'—')],
      ['指标','<code>'+esc(t.indicator||'—')+'</code>'],
      ['频率',esc(t.frequency||'—')],
      ['轨道身份',esc(evalIdentityZh[t.identity]||t.identity||'—')],
      ['是否新鲜 held-out',t.is_fresh===null||t.is_fresh===undefined?'—':(t.is_fresh?'<b>是</b>（新鲜独立留存）':'否（历史回放基准）')]
    ]);
  }
  /* 2. 数据画像：overview.data_profile.{n_obs,first_period,last_period,missing_ratio,overdispersion_ratio,seasonal_strength}（待后端对齐） */
  function renderProfile(d){
    const p=d.data_profile||{};
    $('kgQProfile').innerHTML=compactInfo([
      ['观测数 n_obs','<b>'+esc(p.n_obs??'—')+'</b> 条'],
      ['时间范围',esc(p.first_period||'—')+' 至 '+esc(p.last_period||'—')],
      ['缺失率',pct(p.missing_ratio)],
      ['过离散比（方差/均值）',fmt(p.overdispersion_ratio,2)],
      ['季节强度（lag-12/52 自相关）',fmt(p.seasonal_strength,3)]
    ])+'<p class="kg-plain-note">画像仅统计调优窗口，结构性不含隐藏评估窗的任何标签或指标。</p>';
  }
  /* 3. KG 初始推荐：overview.recommendation.{adapter_id,total_score} + overview.recommendation_reason_zh[]（待后端对齐） */
  function renderRec(d){
    const rec=d.recommendation;
    const reasons=d.recommendation_reason_zh||[];
    if(!rec){$('kgQRec').innerHTML='<p class="kg-empty">暂无 KG 初始推荐记录。</p>';return;}
    let html=compactInfo([
      ['KG 初始推荐模型',`<b>${esc(modelZhName[rec.adapter_id]||rec.adapter_id||'—')}</b><br><code>${esc(rec.adapter_id||'—')}</code>`],
      ['KG 证据分数',fmt(rec.total_score,1)+' / 100'],
      ['重要提示','<b>KG 证据分数代表证据充分程度，不是预测准确率</b>；是否采用由验证与独立测试共同决定']
    ]);
    if(reasons.length) html+='<p><b>中文理由：</b></p><ul>'+reasons.map(r=>`<li>${esc(r)}</li>`).join('')+'</ul>';
    $('kgQRec').innerHTML=html;
  }
  /* 4. Qwen 每轮方案：overview.plans[].{round_index,plan_source,rationale_zh[],kg_evidence_ids[],known_risks[],validation_status,validation_errors[]}（待后端对齐） */
  function renderPlans(d){
    const plans=d.plans||[];
    if(!plans.length){$('kgQPlans').innerHTML='<p class="kg-empty">暂无 Qwen 调优方案记录。</p>';return;}
    $('kgQPlans').innerHTML=plans.map(p=>{
      const src=p.plan_source;
      const srcZh=planSourceZh[src]||src||'—';
      let html=`<h3>第 ${esc(p.round_index??'—')} 轮方案</h3>`;
      html+=compactInfo([
        ['方案来源 plan_source',esc(srcZh)+(src?`（<code>${esc(src)}</code>）`:'')],
        ['校验状态',esc(validationStatusZh[p.validation_status]||p.validation_status||'—')]
      ]);
      const errs=p.validation_errors||[];
      if(errs.length) html+='<p><b>被拒原因：</b></p><ul>'+errs.map(e=>`<li><code>${esc((e&&e.code)||'—')}</code> ${esc((e&&e.message)||e)}</li>`).join('')+'</ul>';
      const rz=p.rationale_zh||[];
      if(rz.length){
        const joined=rz.join('');
        html+=joined.length>120
          ? `<details class="kg-audit-details"><summary>中文理由（${rz.length} 条，内容较长已折叠，点击展开）</summary><ul>${rz.map(r=>`<li>${esc(r)}</li>`).join('')}</ul></details>`
          : '<p><b>中文理由：</b></p><ul>'+rz.map(r=>`<li>${esc(r)}</li>`).join('')+'</ul>';
      }
      const ev=p.kg_evidence_ids||[];
      if(ev.length) html+=`<details class="kg-audit-details"><summary>KG 证据引用（${ev.length} 条）</summary><ul>${ev.map(x=>`<li><code>${esc(x)}</code></li>`).join('')}</ul></details>`;
      const risks=p.known_risks||[];
      if(risks.length) html+=`<details class="kg-audit-details"><summary>已知风险（${risks.length} 条）</summary><ul>${risks.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`;
      return html;
    }).join('');
  }
  /* 5. 参数范围变化：overview.parameter_ranges[].{adapter_id,parameter_name,contract_lower,contract_upper,contract_values,plan_lower,plan_upper,plan_values,source}（待后端对齐） */
  function renderParamRanges(d){
    const ranges=d.parameter_ranges||[];
    if(!ranges.length){$('kgQParamRanges').innerHTML='<p class="kg-empty">暂无参数范围记录。</p>';return;}
    const rows=ranges.map(r=>{
      const contractRange=(r.contract_lower!==null&&r.contract_lower!==undefined)?`${fmt(r.contract_lower,4)} ~ ${fmt(r.contract_upper,4)}`:(Array.isArray(r.contract_values)?esc(r.contract_values.join(' / ')):'—');
      const planRange=(r.plan_lower!==null&&r.plan_lower!==undefined)?`${fmt(r.plan_lower,4)} ~ ${fmt(r.plan_upper,4)}`:(Array.isArray(r.plan_values)?esc(r.plan_values.join(' / ')):'—');
      const src=r.source;
      return [`<code>${esc(r.adapter_id||'—')}</code>`,`<code>${esc(r.parameter_name||'—')}</code>`,contractRange,planRange,esc(parameterSourceZh[src]||src||'—')+(src?`（<code>${esc(src)}</code>）`:'')];
    });
    $('kgQParamRanges').innerHTML=table(['适配器','参数名','合同范围','本轮范围','来源'],rows)
      +'<p class="kg-plain-note">本轮范围是 Qwen 方案在合同范围内的收窄子空间；越出合同范围的方案会被校验器拒绝（而非静默截断）。未提及的参数保持合同默认值，不可调。</p>';
  }
  /* 6. 每轮 trial 数与验证指标：overview.rounds[].{round_index,trials_executed,best_val_wape,best_val_agreement_pct,best_val_coverage,baseline_best_wape,improvement_vs_baseline,target_met}（待后端对齐） */
  function renderRounds(d){
    const rounds=d.rounds||[];
    if(!rounds.length){$('kgQRounds').innerHTML='<p class="kg-empty">暂无调优轮次记录。</p>';return;}
    const rows=rounds.map(r=>{
      const agree=r.best_val_agreement_pct;
      const agreeOk=agree!==null&&agree!==undefined&&Number.isFinite(Number(agree));
      const gap=agreeOk?fmt(TARGET_AGREEMENT-Number(agree),1)+' pp':'—';
      const imp=r.improvement_vs_baseline;
      return [
        '第 '+esc(r.round_index??'—')+' 轮',
        esc(r.trials_executed??'—'),
        fmt(r.best_val_wape),
        agreeOk?fmt(agree,1)+'%':'—',
        pct(r.best_val_coverage),
        fmt(r.baseline_best_wape),
        imp===null||imp===undefined?'—':(Math.round(Number(imp)*10000)/100)+'%',
        gap,
        r.target_met===null||r.target_met===undefined?'—':(r.target_met?'是':'否')
      ];
    });
    const last=rounds[rounds.length-1]||{};
    const baseAgree=last.baseline_best_wape!==null&&last.baseline_best_wape!==undefined&&Number.isFinite(Number(last.baseline_best_wape))
      ? fmt(Math.max(0,1-Number(last.baseline_best_wape))*100,1)+'%':'—';
    $('kgQRounds').innerHTML=table(['轮次','trial 数','最佳验证 WAPE','验证吻合度','区间覆盖率','基线最佳 WAPE','相对 seasonal_naive 等最佳基线改善','距 70% 目标差距','达标'],rows)
      +`<p class="kg-plain-note">${esc(WAPE_NOTE)}验证吻合度来自滚动起点（rolling-origin）验证窗，从未接触独立测试数据；基线吻合度（由基线最佳 WAPE 换算）为 ${esc(baseAgree)}，与验证吻合度、独立测试吻合度是三个不同口径。</p>`;
  }
  /* 7. 调参过程折线图：overview.trials[].{round_index,trial_index,adapter_id,common_wape,gate_pass,failure_reason}（待后端对齐） */
  function drawTuning(d){
    const svg=$('kgQTuning'); svg.innerHTML='';
    const trials=d.trials||[];
    const ok=trials.filter(t=>t.common_wape!==null&&t.common_wape!==undefined&&Number.isFinite(Number(t.common_wape)));
    if(!ok.length){svg.setAttribute('viewBox','0 0 900 40'); $('kgQTuningNote').innerHTML='<p class="kg-empty">暂无可绘制的 trial</p>'; return;}
    const W=900,H=260,PL=52,PR=16,PT=16,PB=34;
    svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const xs=ok.map((t,i)=>i+1), ys=ok.map(t=>Number(t.common_wape));
    const x0=Math.min(...xs),x1=Math.max(...xs);
    let y0=Math.min(...ys),y1=Math.max(...ys); const pad=(y1-y0||0.01)*0.15; y0=Math.max(0,y0-pad); y1=y1+pad;
    const sx=v=>PL+(x1===x0?0:(v-x0)/(x1-x0))*(W-PL-PR);
    const sy=v=>H-PB-((v-y0)/(y1-y0))*(H-PT-PB);
    [0,0.25,0.5,0.75,1].forEach(fr=>{const yv=y0+(y1-y0)*fr; svg.append(mk('line',{x1:PL,y1:sy(yv),x2:W-PR,y2:sy(yv),class:'kg-chart-grid'})); const t=mk('text',{x:PL-6,y:sy(yv)+4,'text-anchor':'end',class:'kg-chart-label'}); t.textContent=String(Math.round(yv*1000)/1000); svg.append(t);});
    const path=ok.map((t,i)=>`${i?'L':'M'}${sx(i+1)},${sy(Number(t.common_wape))}`).join(' ');
    svg.append(mk('path',{d:path,class:'kg-chart-history'}));
    let bestIdx=0; ok.forEach((t,i)=>{if(Number(t.common_wape)<=Number(ok[bestIdx].common_wape)) bestIdx=i;});
    ok.forEach((t,i)=>{
      const failed=t.gate_pass===false||t.failure_reason;
      svg.append(mk('circle',{cx:sx(i+1),cy:sy(Number(t.common_wape)),r:i===bestIdx?7:4,class:failed?'kg-pt-dot-failed':(i===bestIdx?'kg-pt-dot-best':'kg-pt-dot')}));
    });
    const bt=mk('text',{x:sx(bestIdx+1),y:sy(Number(ok[bestIdx].common_wape))-12,'text-anchor':'middle',class:'kg-pt-best-label'}); bt.textContent=`最优 trial（第 ${esc(ok[bestIdx].round_index??'—')} 轮 #${esc(ok[bestIdx].trial_index??'—')}）`; svg.append(bt);
    const xl=mk('text',{x:PL,y:H-8,class:'kg-chart-label'}); xl.textContent='trial 序号 1'; svg.append(xl);
    const xr=mk('text',{x:W-PR,y:H-8,'text-anchor':'end',class:'kg-chart-label'}); xr.textContent=`trial 序号 ${x1}`; svg.append(xr);
    const failedCount=trials.filter(t=>t.gate_pass===false||t.failure_reason).length;
    $('kgQTuningNote').innerHTML=`<p class="kg-plain-note">横轴为全部轮次 trial 的执行顺序，纵轴为共同样本 WAPE（越低越好）；绿色为最优 trial，红色为门禁未通过或失败的 trial（${failedCount} 个）。每轮 trial 0 为合同默认参数。</p>`;
  }
  /* 8. held-out 结果与治理：overview.evaluation.{wape,...,identity,is_fresh,beats_baseline,classification,classification_note_zh,certification,time_chain,registry}（待后端对齐） */
  function renderEval(d){
    const e=d.evaluation;
    if(!e){$('kgQEval').innerHTML='<p class="kg-empty">暂无 held-out 独立测试评估（方案冻结后只评估一次）。</p>';return;}
    const agree=e.agreement_pct===null||e.agreement_pct===undefined?'—':fmt(e.agreement_pct,1)+'%';
    const fresh=e.is_fresh===null||e.is_fresh===undefined?'—':(e.is_fresh?'<b>是</b>（新鲜独立留存）':'<b>否</b>（历史回放/重复评估，不得称为前瞻或盲测认证）');
    const beats=e.beats_baseline===null||e.beats_baseline===undefined?'—':(e.beats_baseline?'<b>是</b>':'否');
    const cert=e.certification;
    const tc=e.time_chain;
    const reg=e.registry;
    let html=`<div class="kg-pt-agreement">独立测试吻合度 ${esc(agree)}</div>`;
    if(e.classification_note_zh) html+=`<p class="kg-pt-disclaimer"><b>${esc(e.classification_note_zh)}</b></p>`;
    html+=`<p class="kg-pt-disclaimer">${esc(WAPE_NOTE)}</p>`
      +table(['指标','数值'],[
        ['WAPE',fmt(e.wape)],['区间覆盖率',pct(e.coverage)],['MAE',fmt(e.mae)],['RMSE',fmt(e.rmse)],
        ['sMAPE',fmt(e.smape)],['MASE',fmt(e.mase)]
      ])
      +compactInfo([
        ['评估集身份',esc(evalIdentityZh[e.identity]||e.identity||'—')+(e.identity?`（<code>${esc(e.identity)}</code>）`:'')],
        ['是否新鲜（fresh）held-out',fresh],
        ['是否真实优于基线（同样本）',beats],
        ['全局消费状态',reg?esc(registryStatusZh[reg.status]||reg.status||'—')+(reg.consumed_at?`<br>消费时间：${esc(reg.consumed_at)}`:'')+(reg.consumer_run_id?`<br>消费 run：<code>${esc(String(reg.consumer_run_id).slice(0,18))}…</code>`:''):'—'],
        ['认证结论',cert?esc(certVerdictZh[cert.verdict]||cert.verdict||'—')+(cert.failed_gates&&cert.failed_gates.length?`<br>未通过门禁：<code>${esc(cert.failed_gates.join(', '))}</code>`:''):(e.is_fresh===false?'不适用（非新鲜窗口不做盲测认证）':'—')],
        ['时间链（锁定→Qwen→调参→消费）',tc?(tc.ok?'<b>成立</b>':'<b>不成立</b>')+(tc.timestamps?`<br>注册 ${esc(tc.timestamps.registered_at||'—')}<br>锁定 ${esc(tc.timestamps.locked_at||'—')}<br>首次 Qwen ${esc(tc.timestamps.first_qwen_started_at||'—')}<br>调参完成 ${esc(tc.timestamps.tuning_finished_at||'—')}<br>消费 ${esc(tc.timestamps.consumed_at||'—')}`:''):'—']
      ]);
    $('kgQEval').innerHTML=html;
  }
  /* 8b. 同样本基线对比：overview.evaluation.paired.{paired_n,paired_sample_match,champion_wape_paired,baselines,best_baseline_id,relative_improvement_vs_best_baseline,paired_csv_sha256}（待后端对齐） */
  function renderPaired(d){
    const e=d.evaluation;
    const p=e&&e.paired;
    if(!p){$('kgQPaired').innerHTML='<p class="kg-empty">暂无同样本配对基线对比记录。</p>';return;}
    const baselines=p.baselines||{};
    const rows=Object.keys(baselines).map(k=>{
      const b=baselines[k]||{};
      const matched=b.sample_matched===true;
      return [
        `<code>${esc(k)}</code>`,
        matched?'<b>一致</b>':'<b>不一致（门禁失败）</b>',
        esc(b.paired_n??'—'),
        fmt(b.wape),pct(b.coverage),
        p.champion_wape_paired!==null&&p.champion_wape_paired!==undefined&&b.wape!==null&&b.wape!==undefined&&Number(b.wape)>0
          ?fmt((Number(b.wape)-Number(p.champion_wape_paired))/Number(b.wape)*100,2)+'%':'—'
      ];
    });
    const imp=p.relative_improvement_vs_best_baseline;
    $('kgQPaired').innerHTML=
      compactInfo([
        ['配对样本数 paired_n','<b>'+esc(p.paired_n??'—')+'</b>（champion 与每个基线必须完全一致）'],
        ['样本一致性门禁',p.paired_sample_match?'<b>通过</b>（全部基线同样本）':'<b>未通过</b>：'+(p.paired_gate_failures&&p.paired_gate_failures.length?'<code>'+esc(p.paired_gate_failures.join('; '))+'</code>':'—')],
        ['Champion 同样本 WAPE',fmt(p.champion_wape_paired)],
        ['最佳基线',p.best_baseline_id?('<code>'+esc(p.best_baseline_id)+'</code> WAPE='+fmt(p.best_baseline_wape)):'—'],
        ['相对最佳基线改善',imp===null||imp===undefined?'—':('<b>'+fmt(Number(imp)*100,2)+'%</b>（≥1% 才算真实优于基线）')],
        ['配对 CSV SHA-256','<code>'+short(p.paired_csv_sha256,16)+'</code>']
      ])
      +table(['基线','样本一致','paired_n','WAPE','覆盖率','Champion 相对改善'],rows)
      +'<p class="kg-plain-note">配对键为 (origin, horizon, target_period)，两侧均要求预测值与真实值非空；样本数不一致即门禁失败，不得计算相对改善。</p>';
  }
  /* 9. Champion：overview.champion.{champion_adapter_id,champion_source,reason}（待后端对齐） */
  function renderChampion(d){
    const c=d.champion;
    if(!c){$('kgQChampion').innerHTML='<p class="kg-empty">暂无 Champion 记录。</p>';return;}
    $('kgQChampion').innerHTML=compactInfo([
      ['当前 Champion',`<b>${esc(modelZhName[c.champion_adapter_id]||c.champion_adapter_id||'—')}</b><br><code>${esc(c.champion_adapter_id||'—')}</code>`],
      ['来源',esc(championSourceZh[c.champion_source]||c.champion_source||'—')],
      ['胜负原因',esc(championReasonZh[c.reason]||c.reason||'—')],
      ['是否已晋级','<b>否</b>（Shadow 决策，永不自动晋级）']
    ])+(c.reason?`<details class="kg-audit-details"><summary>查看原始原因代码</summary><code>${esc(c.reason)}</code></details>`:'');
  }
  /* 10. 反馈成熟进度：overview.feedback_summary.{matured_count,awaiting_count}（待后端对齐）；SVG 属性宽度以符合 style-src 'self' */
  function renderFeedback(d){
    const s=d.feedback_summary||{};
    const m=Number(s.matured_count||0), a=Number(s.awaiting_count||0);
    const total=m+a||1;
    const W=300,H=14;
    const wM=W*m/total, wA=W*a/total;
    const bar=`<svg class="kg-pt-progress" viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="反馈成熟进度">`
      +`<rect class="matured" x="0" y="0" width="${wM}" height="${H}"></rect>`
      +`<rect class="awaiting" x="${wM}" y="0" width="${wA}" height="${H}"></rect></svg>`;
    $('kgQFeedback').innerHTML=bar+compactInfo([
      ['已成熟 MATURED（回放窗真实值已核验）',`<b>${m}</b> 条`],
      ['等待真实值 AWAITING',`<b>${a}</b> 条`]
    ])+'<p class="kg-plain-note">绿色=已成熟，橙色=等待真实值。成熟行标注为历史回放（DEMO_HISTORICAL_REPLAY）。</p>';
  }
  /* 11. learning-cycle 时间线：overview.learning_cycles[].{cycle_type,status,created_at,note,learning_cycle_id}（待后端对齐） */
  function renderCycles(d){
    const cycles=d.learning_cycles||[];
    if(!cycles.length){$('kgQCycles').innerHTML='<p class="kg-empty">暂无学习闭环记录。</p>';return;}
    $('kgQCycles').innerHTML='<ul class="kg-pt-timeline-list">'+cycles.map(c=>
      `<li><b>${esc(cycleTypeZh[c.cycle_type]||c.cycle_type||'—')}</b>`
      +`<br>时间：${esc(c.created_at||'—')}；状态：${esc(c.status||'—')}`
      +(c.note?`<br>${esc(c.note)}`:'')
      +(c.learning_cycle_id?`<br><code>${esc(c.learning_cycle_id)}</code>`:'')
      +'</li>').join('')+'</ul>';
  }
  /* 12. 元信息：overview.run.{qwen_model_id,qwen_model_fingerprint,prompt_template_sha256,snapshot_sha256,plan_validator_version,seed}（待后端对齐） */
  function renderMeta(d){
    const r=d.run||{};
    $('kgQMeta').innerHTML=compactInfo([
      ['Qwen 模型 id','<code>'+esc(r.qwen_model_id||'—')+'</code>'],
      ['模型指纹（前 12 位）','<code>'+short(r.qwen_model_fingerprint)+'</code>'],
      ['Prompt 模板 SHA（前 12 位）','<code>'+short(r.prompt_template_sha256)+'</code>'],
      ['快照 SHA（前 12 位）','<code>'+short(r.snapshot_sha256)+'</code>'],
      ['方案校验器版本','<code>'+esc(r.plan_validator_version||'—')+'</code>'],
      ['随机种子 seed','<code>'+esc(r.seed??'—')+'</code>']
    ])+'<p class="kg-plain-note">同 seed + 同输入可字节级重放；以上哈希用于审计方案、Prompt 与快照的一致性。</p>';
  }
  /* 13. 限制说明：overview.limitations[]（待后端对齐） */
  function renderLimits(d){
    const limits=d.limitations||[];
    $('kgQLimits').innerHTML=limits.length?'<ul>'+limits.map(l=>`<li>${esc(l)}</li>`).join('')+'</ul>':'<p class="kg-empty">—</p>';
  }
  function renderNotRun(msg){
    ['kgQContext','kgQProfile','kgQRec','kgQPlans','kgQParamRanges','kgQRounds','kgQEval','kgQPaired','kgQChampion','kgQFeedback','kgQCycles','kgQMeta','kgQLimits','kgQTuningNote'].forEach(id=>{const el=$(id); if(el) el.innerHTML=`<p class="kg-empty">${esc(msg)}</p>`;});
    const svg=$('kgQTuning'); if(svg){svg.innerHTML=''; svg.setAttribute('viewBox','0 0 900 40');}
  }
  async function loadAll(){
    const t=TRACKS[currentTrack];
    let d;
    try{
      d=await getJSON('/api/v1/kg/autotuning/overview?track_key='+encodeURIComponent(t.key));
    }catch(e){
      if(String(e.message).includes('NOT_FOUND')||String(e.message).includes('HTTP 404')){
        renderNotRun('尚未运行：当前轨道还没有 Qwen 自动调优记录，点击下方“运行 / 刷新”按钮执行（Shadow，幂等）。');
        $('kgQRunInfo').textContent='该轨道尚未运行。';
        return;
      }
      throw e;
    }
    if(!d||!d.run){
      renderNotRun('尚未运行：当前轨道还没有 Qwen 自动调优记录，点击下方“运行 / 刷新”按钮执行（Shadow，幂等）。');
      $('kgQRunInfo').textContent='该轨道尚未运行。';
      return;
    }
    const tasks=[
      ['kgQContext',()=>renderContext(d)],
      ['kgQProfile',()=>renderProfile(d)],
      ['kgQRec',()=>renderRec(d)],
      ['kgQPlans',()=>renderPlans(d)],
      ['kgQParamRanges',()=>renderParamRanges(d)],
      ['kgQRounds',()=>renderRounds(d)],
      ['kgQTuning',()=>drawTuning(d)],
      ['kgQEval',()=>renderEval(d)],
      ['kgQPaired',()=>renderPaired(d)],
      ['kgQChampion',()=>renderChampion(d)],
      ['kgQFeedback',()=>renderFeedback(d)],
      ['kgQCycles',()=>renderCycles(d)],
      ['kgQMeta',()=>renderMeta(d)],
      ['kgQLimits',()=>renderLimits(d)]
    ];
    tasks.forEach(([id,fn])=>{ try{ fn(); }catch(e){ fail(id,e); } });
    const r=d.run||{};
    $('kgQRunInfo').innerHTML=`已加载 ${esc(t.badge)} 的调优运行（run_id <code>${short(r.run_id,18)}</code>；status=${esc(r.status||'—')}；stop_reason=${esc(stopReasonZh[r.stop_reason]||r.stop_reason||'—')}；target_status=${esc(targetStatusZh[r.target_status]||r.target_status||'—')}）。`;
  }
  function setTrack(k){
    currentTrack=k;
    const t=TRACKS[k];
    $('kgQTrackNote').innerHTML=`<b>${esc(t.badge)}</b>（<code>${esc(t.identity)}</code>）：${esc(t.note)}`;
    ['A','B','C','D'].forEach(x=>{const b=$('kgQTrack'+x); if(b) b.setAttribute('aria-pressed',x===k?'true':'false');});
    loadAll().catch(e=>{ $('kgQRunInfo').textContent='Qwen 自动调优面板加载失败：'+e.message+'（不影响本页其他区块）'; renderNotRun('加载失败：'+e.message); });
  }
  $('kgQRun').addEventListener('click',()=>{
    const t=TRACKS[currentTrack];
    const btn=$('kgQRun'); btn.disabled=true;
    $('kgQRunInfo').textContent='正在执行 Qwen 自动调优（Shadow，不写入任何正式数据；首次执行可能较久）…';
    reqJSON('/api/v1/kg/autotuning/run',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':t.runIdemKey},body:JSON.stringify({track_key:t.key})})
      .then(d=>{
        $('kgQRunInfo').innerHTML='运行返回：run_id <code>'+short(d.run_id,18)+'</code>；status='+esc(d.status||'—')
          +'；stop_reason='+esc(stopReasonZh[d.stop_reason]||d.stop_reason||'—')
          +'；target_status='+esc(targetStatusZh[d.target_status]||d.target_status||'—')+'。正在重新加载面板…';
        return loadAll();
      })
      .catch(e=>{$('kgQRunInfo').textContent='执行失败：'+e.message;})
      .finally(()=>{btn.disabled=false;});
  });
  $('kgQTrackA').addEventListener('click',()=>setTrack('A'));
  $('kgQTrackB').addEventListener('click',()=>setTrack('B'));
  $('kgQTrackC').addEventListener('click',()=>setTrack('C'));
  $('kgQTrackD').addEventListener('click',()=>setTrack('D'));
  function start(){ setTrack(currentTrack); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();

/* kg-paper-benchmark-rsos: primary formal peer-reviewed paper benchmark block.
   Renders mpox_rsos2024_paper_benchmark.v1.json (Bleichrodt et al. 2024, RSOS,
   JCR Q1/Q2). Read-only; failures here never affect other sections. */
(()=>{
  'use strict';
  const root=document.getElementById('kg-paper-benchmark-rsos'); if(!root) return;
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const pct=v=>Number.isFinite(Number(v))?Number(v).toFixed(2)+'%':'—';
  const n=v=>Number.isFinite(Number(v))?Number(v).toFixed(4):'—';
  const short=v=>v?String(v).slice(0,16)+'…':'—';
  const table=(headers,rows)=>`<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  async function load(){
    const r=await fetch('/api/v1/forecast-model-knowledge-graph/benchmarks/rsos-mpox-2024',{credentials:'same-origin'});
    const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'}));
    if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);
    const t=d.metrics.by_model_overall||{};
    const best=d.selection&&d.selection.best_test_model?d.selection.best_test_model:'—';
    $('kgBenchmarkRsosStatus').innerHTML=`<b>身份：</b>${esc(d.replication_type)}（${esc(d.evaluation_identity)}）。<b>测试集最优方法：</b>${esc(best)}。<b>配对样本：</b>${esc(d.paired_proof.test_pairs)} 个 (origin,horizon) 配对，全部方法同一集合。`;
    $('kgBenchmarkRsosPaper').innerHTML=table(['字段','值'],[
      ['论文',esc(d.paper.title)],
      ['作者 / 年份',esc(d.paper.authors)+' / '+esc(d.paper.year)],
      ['venue',esc(d.paper.venue)+' '+esc(d.paper.volume)+'('+esc(d.paper.issue)+'):'+esc(d.paper.article_number)],
      ['等级',esc(d.paper.rank_label)],
      ['DOI',`<code>${esc(d.paper.doi)}</code>`],
      ['PDF 版本身份',esc(d.paper.pdf_identity)],
      ['疾病 / 频率 / horizon','论文对象：'+esc(d.task.paper_disease)+'；评估对象：'+esc(d.task.evaluation_disease)+' / '+esc(d.task.frequency)+' / '+esc(d.task.horizons)]
    ]);
    $('kgBenchmarkRsosData').innerHTML=table(['字段','值'],[
      ['数据集',esc(d.dataset.description)],
      ['范围 / 行数',esc(d.dataset.range)+' / '+esc(d.dataset.rows)],
      ['训练 / 验证 / 测试',esc(d.split.train_labels)+' / '+esc(d.split.validation_origins)+' / '+esc(d.split.test_origins)],
      ['数据 SHA-256',`<code>${esc(short(d.dataset.sha256))}</code>`]
    ]);
    const order=d.presentation_order||Object.keys(t);
    $('kgBenchmarkRsosComparators').innerHTML=table(['方法','角色','测试 WAPE','测试吻合度','测试 MAE','测试 RMSE','95%PI 覆盖率','平均区间宽度'],
      order.map(m=>{const x=(t[m]||{}).test||{};const role=(d.methods[m]||{}).role_zh||'';return [esc(m),esc(role),pct(Number(x.wape)*100),pct(x.agreement_pct),n(x.mae),n(x.rmse),pct(Number(x.coverage)*100),n(x.mean_width)];})
    )+'<p class="kg-plain-note">预测吻合度 = max(0, 1 − WAPE) × 100%，不是分类准确率。所有方法在同一 (origin,horizon,target) 配对集合上评估。区间覆盖率必须结合平均区间宽度解读：更宽的区间更容易获得高覆盖率。</p>';
    const sel=d.selection||{};
    $('kgBenchmarkRsosProject').innerHTML=table(['项','值'],[
      ['KG 推荐主模型',esc((d.methods.project_kg_primary||{}).description_zh||'')],
      ['Qwen 受控调优参数',esc(JSON.stringify(sel.qwen_params||{}))],
      ['组合模型权重',esc(JSON.stringify(sel.ensemble_weights||{}))],
      ['选择依据',esc(sel.note_zh||'')],
      ['测试集开启次数',esc(String(sel.test_evaluated_count||''))+'（验证选择冻结后 exactly-once）']
    ]);
    $('kgBenchmarkRsosHorizon').innerHTML=table(['方法','h=1 WAPE','h=2 WAPE','h=3 WAPE','h=4 WAPE','平均区间宽度'],
      order.map(m=>{const hb=(d.metrics.by_horizon_wape||{})[m]||{};const x=(t[m]||{}).test||{};return [esc(m),pct(Number(hb['1'])*100),pct(Number(hb['2'])*100),pct(Number(hb['3'])*100),pct(Number(hb['4'])*100),n(x.mean_width)];})
    );
    const ev=d.evidence||{};
    $('kgBenchmarkRsosAudit').innerHTML=`<p><b>${esc(d.governance_note_zh)}</b></p>`+table(['审计对象','SHA-256'],[
      ['正式 PDF',`<code>${esc(short(ev.pdf_sha256))}</code>`],
      ['EvidenceSpan 定位文件',`<code>${esc(short(ev.spans_sha256))}</code>`],
      ['逐行预测 CSV',`<code>${esc(short(d.paired_proof.predictions_csv_sha256))}</code>`],
      ['配对比较 CSV',`<code>${esc(short(d.paired_proof.paired_csv_sha256))}</code>`],
      ['API 载荷',`<code>${esc(short(d.payload_sha256))}</code>`]
    ])+`<p class="kg-plain-note">不可比因素：${esc((d.incomparability_factors||[]).join('；'))}</p>`
      +`<p class="kg-plain-note">实现缺口：${esc((d.implementation_gaps||[]).join('；'))}</p>`
      +(d.fidelity_note_zh?`<p class="kg-plain-note">Track 1 忠实度验证：${esc(d.fidelity_note_zh)}</p>`:'')
      +`<p class="kg-plain-note">发布状态：${esc(d.publication_state)}。旧 arXiv 预印本 benchmark 保留于下方历史区块，未删除未覆盖。</p>`;
  }
  function start(){load().catch(e=>{$('kgBenchmarkRsosStatus').textContent='正式论文对比链路加载失败：'+e.message;});}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
