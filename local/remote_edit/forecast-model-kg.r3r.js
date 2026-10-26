(function(){
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const status = (state) => `<span class="kg-pill">${esc(state || 'NOT_AVAILABLE_WITH_REASON')}</span>`;
  async function getJSON(url){
    const r = await fetch(url,{credentials:'same-origin'});
    const data = await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'}));
    if(!r.ok) throw new Error(data.detail||r.status);
    return data;
  }
  function modelCard(m, rank){
    return `<div class="kg-card"><h3>#${rank} ${esc(m.display_name||m.name||m.model_id||'模型')}</h3>
      <p><b>当前是否可执行：</b>${esc(m.runnable_now ?? false)}</p>
      <p><b>适用数据：</b>${esc(m.capability||'见条件')}</p>
      <p><b>最低条件：</b>${esc(JSON.stringify(m.minimum_data||{}))}</p>
      <p><b>当前差距：</b>${esc(JSON.stringify(m.current_gap||{}))}</p>
      <p><b>推荐原因：</b>${esc(m.reason||m.why||'由项目规则和证据层计算')}</p>
      <p><b>限制：</b>${esc(m.limitation||m.limitations||'必须经 rolling-origin 验证')}</p>
      <p><b>论文支持：</b>${esc(m.evidence||'PROJECT_RULE_OR_NEARBY_EVIDENCE')}</p></div>`;
  }
  function paperCard(p){
    const loc=p.locator||{};
    return `<div class="kg-paper"><h3>${esc(p.paper_title||p.paper_id)}</h3>
      <p><b>版本：</b>${esc(p.version||'NOT_REPORTED_OR_NOT_EXTRACTED')}</p>
      <p><b>病种/地区/目标/频率/horizon：</b>${esc(p.disease)} / ${esc(p.geography||'nearby/general')} / ${esc(p.target)} / ${esc(p.frequency||'NOT_REPORTED_OR_NOT_EXTRACTED')} / ${esc(p.forecast_horizon||'NOT_REPORTED_OR_NOT_EXTRACTED')}</p>
      <p><b>模型：</b>${esc(p.model)}</p><p><b>结果：</b>${esc(p.result)}；comparator=${esc(p.comparator||'NOT_REPORTED_OR_NOT_EXTRACTED')}</p>
      <p><b>证据角色：</b>${esc(p.evidence_role)}；direct/nearby=${esc(p.eligibility)}；eligibility=${esc(p.evidence_eligibility)}</p>
      <p><b>位置：</b>physical page ${esc(loc.physical_page)} / printed ${esc(loc.printed_page)} / ${esc(loc.section)} / ${esc(loc.table_or_figure)}</p>
      <p><b>anchor：</b>${esc(loc.short_anchor)}</p><p><b>transfer warning：</b>${esc(p.transfer_warning)}</p></div>`;
  }
  function claimCard(c){
    const loc=c.locator||{};
    return `<li><b>${esc(c.claim_text_or_paraphrase)}</b><br>支持/限制：${esc(c.claim_category)} → ${esc(c.model)}；
      位置：p.${esc(loc.physical_page)} ${esc(loc.section)}；相似点：${esc(c.evidence_role)}；
      差异：${esc(c.transfer_warning)}；可参与推荐：${esc(c.eligibility==='direct_candidate'?'direct only after context check':'nearby evidence only')}</li>`;
  }
  function relationTable(sg){
    if(!sg) return `<p>${status('NOT_AVAILABLE_WITH_REASON')} 未找到 DATA_SERIES 或未登录。</p>`;
    return `<p>${status('AVAILABLE')} 节点 ${esc(sg.node_count)}，边 ${esc(sg.edge_count)}</p>
      <table class="kg-table"><tr><th>起点</th><th>关系</th><th>终点</th></tr>${(sg.edges||[]).map(e=>`<tr><td>${esc(e.subject_name)}</td><td>${esc(e.predicate)}</td><td>${esc(e.object_name)}</td></tr>`).join('')}</table>`;
  }
  function provenance(data){
    const p=data.data_profile||{};
    return `<p>${status(p.series_id?'AVAILABLE':'PROVENANCE_LINK_INCOMPLETE')}</p>
      <table class="kg-table"><tr><th>层</th><th>状态</th><th>说明</th></tr>
      <tr><td>DATA_SOURCE</td><td>AVAILABLE</td><td>${esc(p.source_name)}</td></tr>
      <tr><td>SOURCE_CONTRACT → FETCH → RAW → NORMALIZATION → SNAPSHOT</td><td>${esc(p.series_id?'PROVENANCE_LINK_INCOMPLETE':'NOT_AVAILABLE_WITH_REASON')}</td><td>缺失层不伪造；需由 provenance API/源表补链。</td></tr>
      <tr><td>DATA_SERIES</td><td>AVAILABLE</td><td>${esc(p.series_id)}</td></tr>
      <tr><td>QUALITY/REVIEW</td><td>AVAILABLE</td><td>${esc(p.quality_status)} / ${esc(p.review_trust_status)}</td></tr></table>`;
  }
  function evidenceGraph(data){
    const m=data.conditional_full_data_plan?.preferred_model||{};
    const papers=data.paper_matches||[];
    const rows=[['DATA_SERIES','HAS_PROFILE','DATA_PROFILE'],['DATA_PROFILE','PRODUCES','RECOMMENDATION_RESULT'],['RECOMMENDATION_RESULT','PREFERS_CONDITIONALLY',m.display_name||m.name],['RECOMMENDATION_RESULT','USES_PROJECT_RULE','KG_P1_R3R_v1'],['RECOMMENDATION_RESULT','USES_EVIDENCE',data.paper_retrieval?.status||'PAPER_RETRIEVAL_STATUS']].concat(papers.slice(0,3).map(p=>['PAPER','HAS_CLAIM',p.claim_id]));
    return `<table class="kg-table"><tr><th>source</th><th>relation</th><th>target</th></tr>${rows.map(r=>`<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join('')}</table><p>Claim count shown: ${esc((data.claim_matches||[]).length)}；paper1 participation=${esc(data.paper1_participation)}</p>`;
  }
  function renderGraph(data){
    const svg=$('kgGraph'); svg.innerHTML=''; const ns='http://www.w3.org/2000/svg';
    const labels=['DATA_SERIES','DATA_PROFILE','RECOMMENDATION_RESULT',data.conditional_full_data_plan?.preferred_model?.name||'MODEL_CANDIDATE','PAPER','CLAIM'];
    svg.setAttribute('viewBox','0 0 780 260');
    labels.forEach((label,i)=>{ const x=80+i*125,y=130; if(i>0){ const line=document.createElementNS(ns,'line'); line.setAttribute('x1',x-95); line.setAttribute('y1',y); line.setAttribute('x2',x-35); line.setAttribute('y2',y); line.setAttribute('class','kg-edge'); svg.appendChild(line); } const g=document.createElementNS(ns,'g'); const c=document.createElementNS(ns,'circle'); c.setAttribute('cx',x); c.setAttribute('cy',y); c.setAttribute('r',34); c.setAttribute('class','kg-node'); const t=document.createElementNS(ns,'text'); t.setAttribute('x',x); t.setAttribute('y',y+4); t.setAttribute('text-anchor','middle'); t.setAttribute('class','kg-label'); t.textContent=label.slice(0,13); g.appendChild(c); g.appendChild(t); svg.appendChild(g); });
  }
  function render(data, sg){
    const cur=data.current_readiness||{}, plan=data.conditional_full_data_plan||{}, pref=plan.preferred_model||{}, papers=data.paper_matches||[], claims=data.claim_matches||[], hp=data.hyperparameter_evidence||[];
    $('kgStatus').innerHTML=`${status('AVAILABLE')} ${esc(data.resolved_disease?.label_zh||data.request?.disease)} / ${esc(data.series?.display_name||'series unresolved')} · readiness=${esc(cur.status)} · paper=${esc(data.paper_retrieval?.status)}`;
    $('kgDirectAnswer').innerHTML=`<h2>直接结论 / Direct answer</h2><p>因为当前数据画像为 <b>${esc(cur.status)}</b>，观测数 ${esc(cur.facts?.observations)}、覆盖率 ${esc(cur.facts?.coverage ?? 'unknown')}，所以目前只能执行 ${esc((cur.eligible_actions||[]).join('、')) || 'preview'}。数据补齐后首选模型为 <b>${esc(pref.display_name||pref.name)}</b>：${esc(pref.reason)}。注意：论文证据状态为 ${esc(data.paper_retrieval?.status)}，不能把邻近证据当成当前国家/病种的直接验证。</p>`;
    $('kgModels').innerHTML=`<h3>当前数据序列</h3><table class="kg-table">${Object.entries(data.data_profile||{}).slice(0,20).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table><h3>A. 业务关系</h3>${relationTable(sg)}<h3>B. 数据来源与处理血缘</h3>${provenance(data)}<h3>C. 模型推荐与论文证据关系</h3>${evidenceGraph(data)}`;
    const cards=[pref].concat(plan.conditional_alternatives||[]).concat(plan.not_preferred_models||[]);
    $('kgReasons').innerHTML=`<h3>当前数据现在能做什么</h3><p>${status(cur.status)} ${(cur.reasons||[]).map(esc).join('；')}</p><h3>当前 baseline/preview</h3>${(data.current_baselines||[]).map((b,i)=>modelCard(b,i+1)).join('') || '<p>NOT_AVAILABLE_WITH_REASON</p>'}<h3>数据补齐后的首选模型、条件候选和不推荐模型</h3>${cards.map((m,i)=>modelCard(m,i+1)).join('')}<h3>推荐依据</h3><ol>${(data.audit_rationale||[]).map(a=>`<li>${esc(a.fact)}｜source=${esc(a.source)}｜rule=${esc(a.rule_id)}｜effect=${esc(a.effect_on_recommendation)}｜role=${esc(a.evidence_role)}｜limit=${esc(a.limitation)}</li>`).join('')}</ol>`;
    $('kgValidation').innerHTML=`<h3>匹配论文</h3><p>${status(papers.length?'AVAILABLE':data.paper_retrieval?.status)} inventory=${esc(data.paper_retrieval?.inventory_count)} searchable=${esc(data.paper_retrieval?.searchable_text_count)} candidates=${esc(data.paper_retrieval?.candidate_count)} direct=${esc(data.paper_retrieval?.direct_count)} nearby=${esc(data.paper_retrieval?.nearby_count)}</p>${papers.length?papers.map(paperCard).join(''):'<p>NO_DIRECT_PAPER_MATCH_WITH_NEARBY_EVIDENCE：检索已执行，但无直接匹配；请看邻近证据/排除原因。</p>'}<h3>论文知识结论 / Claims</h3><ul>${claims.map(claimCard).join('') || '<li>NOT_AVAILABLE_WITH_REASON</li>'}</ul><h3>论文报告参数 vs 项目建议参数</h3><table class="kg-table"><tr><th>来源类型</th><th>值</th></tr>${hp.map(h=>`<tr><td>${esc(h.source_type)}</td><td>${esc(JSON.stringify(h.value))}</td></tr>`).join('')}</table><h3>数据补齐清单</h3><pre>${esc(JSON.stringify(data.data_gap_plan,null,2))}</pre><h3>验证方案与迁移风险</h3><pre>${esc(JSON.stringify(data.validation_plan,null,2))}</pre>`;
    renderGraph(data); $('kgTableFallback').innerHTML=evidenceGraph(data);
  }
  async function run(){
    const q=new URLSearchParams({disease:$('kgDisease').value,country_or_region:$('kgCountry').value,target:$('kgTarget').value,horizon:$('kgHorizon').value,frequency:($('kgFrequency')?.value||'week')});
    if($('kgDemoProfile')?.value) q.set('demo_profile',$('kgDemoProfile').value);
    $('kgStatus').textContent='LOADING';
    try{ const data=await getJSON('/api/v1/forecast-model-guidance?'+q); let sg=null; if(data.series?.series_id){ try{ sg=await getJSON('/api/v1/kg/datasets/'+encodeURIComponent(data.series.series_id)+'/subgraph'); }catch(e){ sg=null; } } render(data,sg); }
    catch(e){ $('kgStatus').innerHTML=`${status('ERROR_WITH_SAFE_MESSAGE')} ${esc(e.message)}`; $('kgDirectAnswer').innerHTML='<h2>直接结论 / Direct answer</h2><p>ERROR_WITH_SAFE_MESSAGE：无法生成推荐，请检查登录状态和筛选条件。</p>'; }
  }
  document.addEventListener('DOMContentLoaded',()=>{ $('kgRun').addEventListener('click',run); run(); });
})();
