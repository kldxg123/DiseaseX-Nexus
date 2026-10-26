(function(){
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const zh = {
    DIRECT_PAPER_EVIDENCE:'直接论文证据',
    NEARBY_PAPER_EVIDENCE_PLUS_DATA_FIT:'邻近论文证据 + 数据适配',
    PROJECT_RULE_PLUS_DATA_FIT:'项目规则 + 数据适配',
    BASELINE_SAFETY_RULE:'安全基线规则',
    NO_ELIGIBLE_SUPPORT:'没有合格论文 Claim 支持',
    MODEL_SELECTED_WITH_EXPLICIT_LIMITS:'已给出明确模型，但仍需回测验证',
    NO_MATCHING_SERIES:'没有匹配的数据序列',
    QUERY_SERIES_SEMANTIC_MISMATCH:'查询目标与数据序列语义不一致',
    CURRENT_BASELINE_AVAILABLE:'当前可执行安全基线',
    NOT_EXECUTABLE_WITH_REASON:'当前不可执行',
    DIRECT:'直接',
    NEARBY:'邻近',
    INSUFFICIENT_METADATA:'元数据不足',
    MATCH:'匹配',
    COMPATIBLE_GENERAL:'一般兼容',
    MISMATCH:'不匹配',
    NOT_REPORTED:'未报告'
  };
  const keyZh = {
    target_type:'目标类型', time_frequency:'时间频率', history_length:'历史观测数量', coverage_ratio:'时间覆盖率',
    nonzero_count:'非零观测周', geographic_grain:'地理粒度', covariates_available:'协变量状态', forecast_horizon:'预测周期',
    audited_cumulative_to_incident_conversion:'累计病例转周新增审计', negative_revision_handling:'负增量/修订处理',
    leakage_safe_cutoff:'无泄漏时间切分', preserve_missing_weeks:'保留缺失周', add_lag_features:'加入滞后特征',
    add_missingness_flags:'加入缺失标记', optional_versioned_climate_population_covariates:'可选且有版本的气象/人口协变量'
  };
  const human = v => zh[v] || keyZh[v] || String(v ?? '').replaceAll('_',' ');
  const yesNo = v => v === true ? '是' : (v === false ? '否' : esc(v));
  async function getJSON(url){
    const r = await fetch(url, {credentials:'same-origin'});
    const d = await r.json().catch(() => ({detail:'NON_JSON_RESPONSE'}));
    if(!r.ok) throw new Error(d.detail || d.message || `HTTP ${r.status}`);
    return d;
  }
  function table(headers, rows){
    return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function pill(v){ return `<span class="kg-pill">${esc(human(v))}</span>`; }
  function modelLine(m){
    if(!m) return '<b>无明确可执行模型</b>';
    return `<b>${esc(m.display_name_zh || m.canonical_name || '未提供')}</b><br><small>${esc(m.scope || m.recommendation_scope || '')}</small>`;
  }
  function charRows(chars){
    return (chars || []).slice(0,8).map(c => [
      esc(c.display_name || human(c.characteristic_id)),
      esc(yesNo(c.observed)),
      esc(`${c.value ?? '未提供'}${c.unit && c.unit !== 'ratio' ? ' ' + c.unit : ''}`),
      esc(c.effect_on_model_choice || ''),
      esc(c.limitation || '')
    ]);
  }
  function paperRows(papers){
    return (papers || []).map(p => [
      esc(p.paper_title || p.paper_id),
      esc(human(p.directness)),
      esc((p.claim_ids || []).length),
      esc(p.recommendation_participation ? '参与当前证据层' : '仅库存展示')
    ]);
  }
  function claimRows(claims){
    return (claims || []).map(c => [
      esc(c.paper_title || c.paper_id),
      esc(c.claim_text_or_readable_paraphrase || c.claim_id),
      esc(human(c.directness)),
      esc(`${c.locator?.physical_page || '未提供'} / ${c.locator?.printed_page || '未提供'} / ${c.locator?.section || '未提供'}`),
      esc(c.transfer_warning || '')
    ]);
  }
  function chainRows(chains){
    return (chains || []).map(ch => [
      esc(ch.paper_title || ch.paper_id),
      esc(ch.claim_text_or_readable_paraphrase || ch.claim_id),
      esc(human(ch.directness)),
      esc(human(ch.effect_on_recommendation)),
      esc(`${ch.locator?.physical_page || '未提供'} / ${ch.locator?.section || '未提供'}`)
    ]);
  }
  function invRows(inv){
    inv = inv || {};
    return [
      ['历史唯一论文', inv.historical_unique_paper_count || inv.inventory_count || 0],
      ['WORK 节点', inv.work_node_count || 0],
      ['DOCUMENT_VERSION', inv.document_version_count || 0],
      ['可检索文档', inv.searchable_document_count || 0],
      ['合格 Claim', inv.eligible_claim_count || 0],
      ['被排除/隔离 Claim', inv.excluded_paper_or_claim_count || 0],
      ['paper1 participation', 0]
    ].map(r => [esc(r[0]), esc(r[1])]);
  }
  async function renderGraph(data){
    const svg = $('kgGraph'); if(!svg) return;
    svg.innerHTML = '';
    const ns = 'http://www.w3.org/2000/svg';
    let graph;
    try {
      const search = encodeURIComponent(($('kgDisease')?.value || data.series?.disease_id || 'influenza').toLowerCase());
      graph = await getJSON(`/api/v1/forecast-model-kg/ux3/graph?search=${search}&layer=all&max_nodes=40&max_edges=80`);
    } catch(e) {
      $('kgTableFallback').innerHTML = `<p class="kg-error">图谱 API 加载失败：${esc(e.message)}</p>`;
      return;
    }
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    svg.setAttribute('viewBox','0 0 960 420');
    const width = 900, cx = 480, cy = 205, radius = 150;
    const pos = {};
    nodes.forEach((n,i) => {
      const angle = (Math.PI * 2 * i) / Math.max(1,nodes.length);
      pos[n.id] = {x: cx + Math.cos(angle)*radius, y: cy + Math.sin(angle)*radius};
    });
    edges.forEach(e => {
      if(!pos[e.source_id] || !pos[e.target_id]) return;
      const line = document.createElementNS(ns,'line');
      line.setAttribute('x1',pos[e.source_id].x); line.setAttribute('y1',pos[e.source_id].y);
      line.setAttribute('x2',pos[e.target_id].x); line.setAttribute('y2',pos[e.target_id].y);
      line.setAttribute('class','kg-edge');
      const title = document.createElementNS(ns,'title'); title.textContent = `${e.relation_type} · ${e.status}`;
      line.append(title); svg.append(line);
    });
    nodes.forEach((n,i) => {
      const p = pos[n.id] || {x: 40+i*20, y: 40};
      const g = document.createElementNS(ns,'g'); g.setAttribute('tabindex','0'); g.setAttribute('class','kg-node-group');
      const c = document.createElementNS(ns,'circle'); c.setAttribute('cx',p.x); c.setAttribute('cy',p.y); c.setAttribute('r',24); c.setAttribute('class',`kg-node kg-node-${esc(n.type || 'UNKNOWN')}`);
      const title = document.createElementNS(ns,'title'); title.textContent = `${n.label} · ${n.type} · ${n.status}`;
      const t = document.createElementNS(ns,'text'); t.setAttribute('x',p.x); t.setAttribute('y',p.y+39); t.setAttribute('text-anchor','middle'); t.setAttribute('class','kg-label'); t.textContent = String(n.label || n.id).slice(0,28);
      g.append(c,title,t); svg.append(g);
    });
    $('kgTableFallback').innerHTML = `<p>真实图谱 API：returned_nodes=${graph.returned_nodes}, returned_edges=${graph.returned_edges}, truncated=${graph.truncated}, hash=${esc((graph.graph_hash||'').slice(0,16))}…</p>` +
      table(['节点','类型','状态','图层'], nodes.slice(0,20).map(n=>[esc(n.label), esc(n.type), esc(n.status), esc(n.source_layer)])) +
      table(['边','关系','状态'], edges.slice(0,20).map(e=>[esc(`${e.source_id.slice(0,18)} → ${e.target_id.slice(0,18)}`), esc(e.relation_type), esc(e.status)]));
  }
  function render(data){
    const decision = data.recommendation_decision || {};
    const current = data.current_executable_model;
    const primary = data.conditional_primary_model;
    const paperStatus = data.paper_retrieval?.paper_support_status || data.paper_retrieval?.status;
    const preprocessing = data.required_preprocessing || [];
    $('kgStatus').innerHTML = `${pill(decision.status)} ${esc(data.series?.display_name || '无匹配序列')} · ${pill(paperStatus)} · policy=${esc(data.versions?.recommendation_policy || '')}`;
    $('kgDirectAnswer').innerHTML = `<h2>直接答案 / Direct answer</h2>
      <div class="kg-answer-grid">
        <div><span>当前可执行模型</span>${modelLine(current)}</div>
        <div><span>数据补齐后的首选模型</span>${modelLine(primary)}</div>
        <div><span>当前状态</span><b>${esc(human(decision.status))}</b></div>
        <div><span>论文证据强度</span><b>${esc(human(paperStatus))}</b></div>
      </div>
      <p><b>为什么：</b>${esc(decision.one_sentence || primary?.one_sentence_reason || '未提供')}</p>`;
    $('kgModels').innerHTML = `<h3>它针对什么数据</h3>${table(['特点','是否已观测','实际值','对模型选择的影响','限制'], charRows(data.data_characteristics))}
      <h3>查询/series 语义一致性</h3>${table(['字段','值'], Object.entries(data.query_series_semantics || {}).map(([k,v]) => [esc(human(k)), esc(yesNo(v))]))}
      <h3>论文库存（不等于推荐证据）</h3>${table(['库存项','数量'], invRows(data.paper_inventory_coverage))}`;
    $('kgReasons').innerHTML = `<h3>为什么推荐这个模型</h3>
      <p><b>数据事实：</b>${(data.data_characteristics || []).slice(0,4).map(c => `${c.display_name}=${c.value}`).join('；') || '无匹配数据'}</p>
      <p><b>模型适配：</b>${(data.model_data_fit || []).slice(0,4).map(f => `${human(f.data_characteristic_id)} → ${f.explanation}`).join('；') || '未建立适配链'}</p>
      <p><b>证据强度：</b>${esc(human(paperStatus))}</p>
      <h3>当前可执行与候选模型</h3>
      ${table(['层级','模型','说明'], [
        ['当前可执行', modelLine(current), esc(current?.reason || '当前无可执行模型')],
        ['条件首选', modelLine(primary), esc(primary?.one_sentence_reason || '')],
        ...((data.ranked_alternatives || []).map((m,i)=>[`候选 #${i+2}`, modelLine(m), esc(m.reason || '')])),
        ...((data.not_recommended_models || []).map(m=>['当前不推荐', esc(m.display_name_zh || m.model_id), esc(m.reason || '')]))
      ])}
      <h3>预处理（不是模型名）</h3><ul>${preprocessing.map(x=>`<li>${esc(human(x))}</li>`).join('') || '<li>暂无额外预处理</li>'}</ul>`;
    $('kgValidation').innerHTML = `<h3>当前推荐证据：论文 → Claim → locator</h3>
      ${data.evidence_chains?.length ? table(['论文','Claim','direct/nearby','作用','定位'], chainRows(data.evidence_chains)) : '<p>当前没有合格论文 Claim 直接支持该首选模型。该建议来自当前数据画像和版本化项目规则，仍需 rolling-origin 回测验证。</p>'}
      <h3>论文实体（按 paper_id 去重）</h3>${table(['论文标题','证据类型','Claim 数','参与状态'], paperRows(data.paper_matches))}
      <h3>Claim 实体（独立于论文实体）</h3>${table(['论文','Claim 可读结论','direct/nearby','证据位置','迁移提醒'], claimRows(data.claim_matches))}
      <h3>验证要求</h3><ul>${(data.validation_requirements || []).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
    renderGraph(data);
    $('kgTableFallback').innerHTML += table(['图层关系','说明'], [
      ['DATA_SERIES → DATA_CHARACTERISTIC', '当前序列的实际数据特点'],
      ['MODEL → SUITED_FOR → DATA_CHARACTERISTIC', '模型适配的数据事实'],
      ['PAPER → HAS_CLAIM → CLAIM → SUPPORTED_BY → EVIDENCE_SPAN', '论文和 Claim 分离，Claim 有 locator'],
      ['RECOMMENDATION → USES_RULE / USES_CLAIM / PREFERS_MODEL', '推荐证据层来自后端合同']
    ]);
  }
  function queryString(){
    const q = new URLSearchParams({
      disease:$('kgDisease').value.trim(),
      country_or_region:$('kgCountry').value.trim(),
      target:$('kgTarget').value.trim(),
      frequency:$('kgFrequency').value.trim(),
      horizon:$('kgHorizon').value || '4'
    });
    const demo = $('kgDemoProfile')?.value || ''; if(demo) q.set('demo_profile', demo);
    return q;
  }
  async function run(){
    $('kgStatus').textContent = '正在生成明确模型建议…';
    try{ render(await getJSON('/api/v1/forecast-model-guidance?' + queryString())); }
    catch(e){ $('kgStatus').innerHTML = `${pill('PAPER_RETRIEVAL_FAILED')} ${esc(e.message)}`; $('kgDirectAnswer').innerHTML = '<h2>直接答案</h2><p>无法生成推荐；不会复用语义不一致的数据序列。</p>'; }
  }
  async function postJSON(url){
    const r = await fetch(url, {method:'POST', credentials:'same-origin'});
    const d = await r.json().catch(() => ({detail:'NON_JSON_RESPONSE'}));
    if(!r.ok) throw new Error(d.detail || d.message || `HTTP ${r.status}`);
    return d;
  }
  function renderUx3(payload){
    const run = payload.run || {};
    const trace = run.selection_trace || payload.result?.trace || {};
    const profile = run.profile || payload.result?.profile || {};
    const vals = payload.values || [];
    const candidates = trace.candidate_models || [];
    const rejected = trace.rejected_models || [];
    $('ux3Status').innerHTML = `${pill(run.mode)} ${pill(run.status)} · run=${esc(String(run.run_id||'').slice(0,8))}… · promotion_eligible=${esc(run.promotion_eligible)}`;
    $('ux3Profile').innerHTML = table(['字段','值'], Object.entries(profile).map(([k,v])=>[esc(human(k)), esc(v)]));
    $('ux3Candidates').innerHTML = table(['模型','准入','folds','MAE','WAPE'], candidates.map(c=>[
      esc(c.model_name), esc(c.admission_reason),
      esc(c.backtest_metrics?.fold_count ?? 0),
      esc(c.backtest_metrics?.mae == null ? 'NULL' : Number(c.backtest_metrics.mae).toFixed(3)),
      esc(c.backtest_metrics?.wape == null ? 'NULL' : (Number(c.backtest_metrics.wape)*100).toFixed(2)+'%')
    ])) + (rejected.length ? `<h4>被拒绝模型</h4>${table(['模型','原因'], rejected.map(r=>[esc(r.model_name), esc(r.rejection_reason)]))}` : '');
    $('ux3Metrics').innerHTML = table(['字段','值'], [
      ['champion_model', esc(trace.baseline_guard?.champion_model || run.model_name)],
      ['selection_reason_code', esc(trace.baseline_guard?.reason_code || trace.recommendation_reason || '')],
      ['approved KG candidate count', esc(trace.kg_approved_candidate_count ?? 0)],
      ['fallback reason', esc(trace.fallback_reason || 'NONE')],
      ['CDC R4F allowed', esc(trace.cdc_r4f_allowed)]
    ]);
    $('ux3Forecast').innerHTML = table(['类型','目标期','horizon','预测','下界','上界','实际值'], vals.map(v=>[
      esc(v.row_type), esc(v.target_period_start), esc(v.horizon),
      esc(v.yhat), esc(v.lower_value), esc(v.upper_value), esc(v.actual_value ?? 'NULL')
    ]));
  }
  async function runUx3(){
    $('ux3Status').textContent = '正在运行或读取 DEMO_SHADOW…';
    const q = new URLSearchParams({
      country: $('ux3Country').value.trim(),
      disease: $('ux3Disease').value.trim(),
      metric: $('ux3Metric').value.trim(),
      cadence: $('ux3Cadence').value.trim()
    });
    try { renderUx3(await postJSON('/api/v1/forecast-model-kg/ux3/demo-shadow-run?' + q)); }
    catch(e){ $('ux3Status').innerHTML = `${pill('DEMO_SHADOW_FAILED')} ${esc(e.message)}`; }
  }
  $('kgRun').addEventListener('click', run);
  $('ux3Run')?.addEventListener('click', runUx3);
  run();
  runUx3();
})();
