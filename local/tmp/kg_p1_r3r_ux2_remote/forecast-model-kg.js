(function(){
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  const modelNames = {
    gradient_boosted_trees_xgboost_after_data_completion: '补齐数据后的梯度提升树 / XGBoost',
    regularized_negative_binomial_or_gam: '正则化负二项回归 / GAM',
    graph_neural_network: '图神经网络',
    deep_sequence_model: '深度序列模型',
    audited_cumulative_to_incident_then_baseline_glm_gam_or_boosting: '先审计累计转新增，再比较 baseline / GLM-GAM / 树模型',
    state_space_or_ensemble_after_conversion: '转换后的状态空间或集成模型',
    direct_model_on_cases_cumulative: '直接对累计病例建模',
    last_observed_value: '最后观测值基线',
    seasonal_naive: '季节性 naive 基线',
    seasonal_naive_then_regularized_baseline: '季节性基线优先'
  };
  const fieldNames = {
    history_weeks: '历史长度',
    coverage: '时间覆盖率',
    recent_coverage: '近期覆盖率',
    nonzero_weeks: '非零观测周',
    covariates: '所需协变量',
    covariates_gap: '当前缺少协变量',
    history_weeks_gap: '历史长度差距',
    coverage_gap: '覆盖率差距',
    nonzero_weeks_gap: '非零周差距',
    audited_conversion: '累计转新增审计',
    leakage_safe_cutoff: '无泄漏时间切分',
    target_conversion: '目标口径转换',
    negative_revision_check: '负增量/修订检查',
    baseline_first: '先比较基线',
    horizon_weeks: '预测 horizon',
    frequency: '频率',
    temperature: '气温',
    rainfall: '降雨',
    humidity: '湿度',
    population_or_region_panel: '人口或地区面板',
    spatial_panel: '空间面板',
    at_least_one_observation: '至少一个观测值',
    converted_weekly_cases: '已转换为周新增病例'
  };
  const statusNames = {
    FORMAL_READY_CANDIDATE: '形式上具备建模候选资格（仍需回测）',
    BASELINE_ONLY: '当前只适合 baseline / 预览',
    PREVIEW_ONLY: '当前仅适合预览，暂不进入正式预测',
    DIRECT_MATCH_AVAILABLE: '存在直接匹配论文证据',
    NO_DIRECT_MATCH_NEARBY_AVAILABLE: '没有直接匹配论文，但存在邻近论文证据',
    NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE: '没有直接匹配论文，但存在邻近论文证据',
    NO_ELIGIBLE_PAPER_AFTER_COMPLETED_SEARCH: '检索已完成，未找到合格直接或邻近论文',
    PAPER_INDEX_NOT_READY: '论文索引尚未就绪，暂不能判断',
    PAPER_RETRIEVAL_FAILED: '论文检索失败，暂不能判断',
    AVAILABLE: '可用',
    true: '当前可执行',
    false: '当前暂不可执行'
  };
  const actionNames = {
    show_preview: '展示预览',
    run_baseline_comparison_only: '仅运行 baseline 对比',
    not_production_validated: '尚未生产级验证',
    not_public_health_forecast: '不是公共卫生决策预测'
  };

  async function getJSON(url){
    const r = await fetch(url, {credentials:'same-origin'});
    const data = await r.json().catch(() => ({detail:'NON_JSON_RESPONSE'}));
    if(!r.ok) throw new Error(data.detail || data.message || `HTTP ${r.status}`);
    return data;
  }

  function humanKey(k){ return fieldNames[k] || String(k || '').replaceAll('_',' '); }
  function humanStatus(s){ return statusNames[s] || String(s || '').replaceAll('_',' '); }
  function humanAction(s){ return actionNames[s] || humanStatus(s); }
  function modelDisplay(m){
    if(!m) return '未提供';
    const id = typeof m === 'string' ? m : (m.display_name || m.name || m.model_id);
    return modelNames[id] || id || '未提供';
  }
  function emptyText(context){
    if(context === 'minimum') return '暂无额外最低条件';
    if(context === 'gap') return '当前无已识别差距';
    return '未提供';
  }
  function valueText(value, context){
    if(value === null || value === undefined || value === '') return emptyText(context);
    if(typeof value === 'boolean') return value ? '当前可执行' : '当前暂不可执行';
    if(Array.isArray(value)) return value.length ? value.map(v => fieldNames[v] || humanStatus(v)).join('、') : emptyText(context);
    if(typeof value === 'object'){
      const entries = Object.entries(value);
      if(!entries.length) return emptyText(context);
      return entries.map(([k,v]) => `${humanKey(k)}：${valueText(v, context)}`).join('；');
    }
    return humanStatus(value);
  }
  function table(headers, rows, cls='kg-table'){
    return `<div class="table-wrap"><table class="${cls}"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function pill(text, cls=''){ return `<span class="kg-pill ${esc(cls)}">${esc(humanStatus(text))}</span>`; }

  function modelCard(m, rank, kind){
    if(!m) return '';
    return `<div class="kg-card model-card">
      <h3>#${rank} ${esc(modelDisplay(m))}</h3>
      <p class="muted">${esc(kind || '')}</p>
      <p><b>当前状态：</b>${esc(valueText(m.current_executable))}</p>
      <p><b>能力：</b>${esc(m.capability || m.reason || '未提供')}</p>
      <p><b>最低条件：</b>${esc(valueText(m.minimum_data, 'minimum'))}</p>
      <p><b>当前差距：</b>${esc(valueText(m.current_gap, 'gap'))}</p>
      <p><b>限制：</b>${esc(m.limitation || m.reason || '需由 rolling-origin 回测验证')}</p>
    </div>`;
  }

  function paperConclusion(data){
    const pr = data.paper_retrieval || {};
    const direct = Number(pr.direct_count || 0);
    const nearby = Number(pr.nearby_count || 0);
    const claims = Number(pr.eligible_claim_count || pr.candidate_count || 0);
    if(pr.status === 'DIRECT_MATCH_AVAILABLE'){
      return `存在 ${direct} 篇直接匹配论文，当前展示合格 Claim ${claims} 条。`;
    }
    if(pr.status === 'NO_DIRECT_MATCH_NEARBY_AVAILABLE' || pr.status === 'NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE'){
      return `当前本地论文证据库中，未找到同时匹配 ${data.series?.display_name || '当前序列'} 且通过证据资格校验的直接论文；但已找到 ${nearby} 篇邻近论文、${claims} 条合格 Claim。因此当前推荐由项目规则和邻近论文证据共同支持，并非“没有相关论文”。`;
    }
    if(pr.status === 'NO_ELIGIBLE_PAPER_AFTER_COMPLETED_SEARCH'){
      return `论文检索已完成，但当前证据库没有可参与推荐的直接或邻近论文；当前模型建议仅来自版本化项目规则。`;
    }
    if(pr.status === 'PAPER_INDEX_NOT_READY'){
      return `论文索引尚未就绪，当前不能判断是否存在相关论文。`;
    }
    return `论文检索失败或状态未知，当前不能作出“没有相关论文”的结论。`;
  }

  function paperCard(p){
    const loc = p.locator || {};
    return `<div class="kg-paper">
      <h3>${esc(p.paper_title || p.paper_id || '论文')}</h3>
      <p>${pill(p.eligibility || p.evidence_eligibility)} ${pill(p.evidence_role || '')}</p>
      <p><b>Claim：</b>${esc(p.claim_text_or_paraphrase || '未提供')}</p>
      <p><b>模型/结果：</b>${esc(valueText(p.model))}；${esc(valueText(p.result))}</p>
      <p><b>定位：</b>物理页 ${esc(loc.physical_page || '未提供')} / 印刷页 ${esc(loc.printed_page || '未提供')} / ${esc(loc.section || '未提供')} / ${esc(loc.table_or_figure || '未提取')}</p>
      <p><b>迁移提醒：</b>${esc(p.transfer_warning || '邻近证据需谨慎迁移。')}</p>
    </div>`;
  }
  function claimCard(c){
    const loc = c.locator || {};
    return `<li><b>${esc(c.claim_text_or_paraphrase || c.claim_id)}</b><br>
      证据角色：${esc(c.evidence_role || c.claim_category || '未提供')}；定位：物理页 ${esc(loc.physical_page || '未提供')} / 印刷页 ${esc(loc.printed_page || '未提供')} / ${esc(loc.section || '未提供')}
    </li>`;
  }
  function relationTable(sg){
    if(!sg || !Array.isArray(sg.edges) || !sg.edges.length) return '<p>业务关系图暂不可用，但推荐合同仍可查看。</p>';
    const relationZh = {ABOUT_GEOGRAPHY:'关于国家/地区', ABOUT_DISEASE:'关于病种', PREDICTS_TARGET:'预测目标', USES_SOURCE:'使用数据源'};
    const rows = sg.edges.map(e => [
      `<span title="${esc(e.subject_id || '')}">${esc(e.subject_name || shortId(e.subject_id))}</span>`,
      `<b>${esc(relationZh[e.predicate] || e.predicate)}</b><br><small>${esc(e.predicate || '')}</small>`,
      `<span title="${esc(e.object_id || '')}">${esc(e.object_name || shortId(e.object_id))}</span>`
    ]);
    return table(['起点','关系','终点'], rows);
  }
  function shortId(v){ return v ? String(v).slice(0,8) + '…' : '未提供'; }
  function provenance(data){
    const p=data.data_profile||{};
    return table(['层','状态','说明'], [
      ['DATA_SERIES', '可用', esc(data.series?.display_name || '当前序列未解析')],
      ['SOURCE/RAW', '血缘不完整', esc(p.source_name || '未提供来源名')],
      ['QUALITY/REVIEW', '可用', esc(`${p.quality_status || '未提供'} / ${p.review_trust_status || '未提供'}`)]
    ]);
  }
  function evidenceGraph(data){
    const m=data.conditional_full_data_plan?.preferred_model||{};
    const papers=data.paper_matches||[];
    const rows=[
      ['数据序列','具有画像','数据画像'],
      ['数据画像','生成','推荐结果'],
      ['推荐结果','条件首选', esc(modelDisplay(m))],
      ['推荐结果','使用项目规则','KG_P1_R3R_UX1_v1'],
      ['推荐结果','论文状态', esc(humanStatus(data.paper_retrieval?.status))]
    ].concat(papers.slice(0,3).map(p=>['论文','包含 Claim', `<span title="${esc(p.claim_id)}">${esc(shortId(p.claim_id))}</span>`]));
    return table(['source','relation','target'], rows) + `<p class="muted">当前显示 Claim 数：${esc((data.claim_matches||[]).length)}；paper1 participation = 0</p>`;
  }

  function renderGraph(data){
    const svg=$('kgGraph'); if(!svg) return;
    svg.innerHTML=''; const ns='http://www.w3.org/2000/svg';
    const labels=['DATA_SERIES','DATA_PROFILE','RECOMMENDATION_RESULT',modelDisplay(data.conditional_full_data_plan?.preferred_model),'PAPER','CLAIM'];
    labels.forEach((lab,i)=>{ const g=document.createElementNS(ns,'g'); const x=70+i*145, y=120+(i%2)*70; const c=document.createElementNS(ns,'circle'); c.setAttribute('cx',x); c.setAttribute('cy',y); c.setAttribute('r',34); c.setAttribute('class','kg-node'); const t=document.createElementNS(ns,'text'); t.setAttribute('x',x); t.setAttribute('y',y+54); t.setAttribute('text-anchor','middle'); t.setAttribute('class','kg-label'); t.textContent=lab; g.append(c,t); svg.append(g); });
  }

  function render(data, sg){
    const cur=data.current_readiness||{}, plan=data.conditional_full_data_plan||{}, pref=plan.preferred_model||{}, papers=data.paper_matches||[], claims=data.claim_matches||[], hp=data.hyperparameter_evidence||[];
    $('kgStatus').innerHTML=`${pill('AVAILABLE')} ${esc(data.resolved_disease?.label_zh||data.request?.disease)} / ${esc(data.series?.display_name||'series unresolved')} · ${esc(humanStatus(cur.status))} · ${esc(humanStatus(data.paper_retrieval?.status))}`;
    $('kgDirectAnswer').innerHTML=`<h2>直接结论 / Direct answer</h2><p>因为当前数据画像为 <b>${esc(humanStatus(cur.status))}</b>，观测数 ${esc(cur.facts?.observations ?? '未知')}、覆盖率 ${esc(cur.facts?.coverage ?? '未知')}，所以目前建议执行 ${esc((cur.eligible_actions||[]).map(humanAction).join('、') || '展示预览')}。数据补齐后首选模型为 <b>${esc(modelDisplay(pref))}</b>：${esc(pref.reason || '需按项目规则和回测验证决定。')}</p><p><b>论文结论：</b>${esc(paperConclusion(data))}</p>`;
    $('kgModels').innerHTML=`<h3>当前数据序列</h3>${table(['字段','含义'], Object.entries(data.data_profile||{}).slice(0,20).map(([k,v])=>[esc(humanKey(k)), esc(valueText(v))]))}<h3>A. 业务关系</h3>${relationTable(sg)}<h3>B. 数据来源与处理血缘</h3>${provenance(data)}<h3>C. 模型推荐与论文证据关系</h3>${evidenceGraph(data)}`;
    const cards = []
      .concat((data.current_baselines||[]).map((m,i)=>modelCard(m,i+1,'当前 baseline / preview')))
      .concat([modelCard(pref,1,'数据补齐后的条件首选')])
      .concat((plan.conditional_alternatives||[]).map((m,i)=>modelCard(m,i+2,'条件候选')))
      .concat((plan.not_preferred_models||[]).map((m,i)=>modelCard(m,i+1,'当前不推荐')));
    $('kgReasons').innerHTML=`<h3>当前数据现在能做什么</h3><p>${pill(cur.status)} ${(cur.reasons||[]).map(esc).join('；')}</p><h3>模型建议</h3>${cards.join('') || '<p>推荐合同为空：请查看结构化错误。</p>'}<h3>推荐依据</h3><ol>${(data.audit_rationale||[]).map(a=>`<li>${esc(a.fact)}｜来源：${esc(a.source)}｜规则：${esc(a.rule_id)}｜作用：${esc(valueText(a.effect_on_recommendation))}｜限制：${esc(a.limitation)}</li>`).join('')}</ol>`;
    $('kgValidation').innerHTML=`<h3>论文检索结论</h3><p>${pill(data.paper_retrieval?.status)} ${esc(paperConclusion(data))}</p>${table(['计数项','数量'], [['direct_eligible_paper_count', data.paper_retrieval?.direct_count], ['nearby_eligible_paper_count', data.paper_retrieval?.nearby_count], ['eligible_claim_count', data.paper_retrieval?.eligible_claim_count], ['excluded_paper_or_claim_count', data.paper_retrieval?.excluded_paper_or_claim_count], ['inventory_count', data.paper_retrieval?.inventory_count], ['searchable_text_count', data.paper_retrieval?.searchable_text_count]].map(([k,v])=>[esc(k), esc(v ?? 0)]))}<h3>直接或邻近论文</h3>${papers.length?papers.map(paperCard).join(''):'<p>当前没有可展示论文；请查看上方论文状态。</p>'}<h3>论文知识结论 / Claims</h3><ul>${claims.map(claimCard).join('') || '<li>没有可展示的合格 Claim。</li>'}</ul><h3>论文报告参数 vs 项目建议参数</h3>${table(['来源类型','可读值'], hp.map(h=>[esc(valueText(h.source_type)), esc(valueText(h.value))]))}<h3>数据补齐清单</h3><ul>${((data.data_gap_plan||{}).items||[]).map(x=>`<li>${esc(x)}</li>`).join('') || '<li>当前无已识别差距。</li>'}</ul><h3>验证方案与迁移风险</h3>${table(['项目','说明'], Object.entries(data.validation_plan||{}).map(([k,v])=>[esc(humanKey(k)), esc(valueText(v))]))}`;
    renderGraph(data);
    $('kgTableFallback').innerHTML=evidenceGraph(data);
  }

  function queryString(){
    const q=new URLSearchParams({
      disease:$('kgDisease').value.trim(),
      country_or_region:$('kgCountry').value.trim(),
      target:$('kgTarget').value.trim(),
      frequency:$('kgFrequency').value.trim(),
      horizon:$('kgHorizon').value || '4'
    });
    const demo=$('kgDemoProfile')?.value || '';
    if(demo) q.set('demo_profile', demo);
    return q;
  }
  async function run(){
    $('kgStatus').textContent='正在生成建议…';
    try{
      const data=await getJSON('/api/v1/forecast-model-guidance?'+queryString());
      let sg=null;
      if(data.series?.series_id){
        try{ sg=await getJSON('/api/v1/kg/datasets/'+encodeURIComponent(data.series.series_id)+'/subgraph'); }catch(e){ sg=null; }
      }
      render(data, sg);
    }catch(e){
      $('kgStatus').innerHTML=`${pill('PAPER_RETRIEVAL_FAILED')} ${esc(e.message)}`;
      $('kgDirectAnswer').innerHTML='<h2>直接结论 / Direct answer</h2><p>无法生成推荐；这不是“没有论文”，而是当前请求或服务返回了结构化错误。</p>';
    }
  }
  $('kgRun').addEventListener('click', run);
  run();
})();
