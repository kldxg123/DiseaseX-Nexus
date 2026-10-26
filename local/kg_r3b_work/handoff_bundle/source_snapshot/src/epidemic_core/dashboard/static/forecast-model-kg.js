(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  async function getJSON(url){const r=await fetch(url,{credentials:'same-origin'}); const d=await r.json().catch(()=>({detail:'NON_JSON_RESPONSE'})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d;}
  function table(headers,rows){return `<div class="table-wrap"><table class="kg-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  function unique(a){return [...new Set(a.filter(Boolean))].sort();}
  let catalog=[];
  function fillSelect(id, values){const el=$(id); el.innerHTML=values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');}
  async function loadCatalog(){
    const d=await getJSON('/api/v1/forecast-model-kg/r3a/preexecution/catalog'); catalog=d.items||[];
    fillSelect('kgCountry', unique(catalog.map(x=>x.country_alpha3)));
    fillSelect('kgDisease', unique(catalog.map(x=>x.disease_slug)));
    fillSelect('kgMetric', unique(catalog.map(x=>x.metric_code)));
    fillSelect('kgCadence', unique(catalog.map(x=>x.cadence)));
  }
  async function loadCandidates(){
    const q=new URLSearchParams({country:$('kgCountry').value,disease:$('kgDisease').value,metric:$('kgMetric').value,cadence:$('kgCadence').value});
    const d=await getJSON('/api/v1/forecast-model-kg/r3a/preexecution/candidates?'+q);
    $('kgStatus').innerHTML=`<b>PRE_EXECUTION_EVIDENCE_SCORE</b> ? ${d.items.length} ?? baseline ?? ? baseline_in_preexecution_recommendation_count=${d.baseline_in_preexecution_recommendation_count}`;
    if(!d.items.length){$('kgModels').textContent='NO_MATCHING_DATASET'; return;}
    const rows=d.items.map(x=>[`<button data-cid="${esc(x.candidate_id)}" class="kg-detail">??????</button>`,esc(x.display_name),esc(x.preexecution_evidence_score),esc(x.parameter_confidence_score),esc(x.evidence_coverage),esc(x.explanation_zh),esc(x.local_backtest_status),esc(x.forecast_run_id||'null')]);
    $('kgModels').innerHTML=table(['??','??????','????????','?????','????','??','????','forecast_run_id'], rows);
    document.querySelectorAll('.kg-detail').forEach(b=>b.addEventListener('click',()=>loadDetail(b.dataset.cid)));
    await loadDetail(d.items[0].candidate_id);
  }
  async function loadDetail(cid){
    const d=await getJSON('/api/v1/forecast-model-kg/r3a/preexecution/candidates/'+encodeURIComponent(cid));
    const c=d.candidate, comps=d.score_components||[], ev=d.evidence_points||[];
    $('kgWaterfall').innerHTML=table(['?','??','??','??','??'], comps.map(x=>[esc(x.component_group),esc(x.rule_id),esc(x.score),esc(x.max_score),esc(x.contribution)]));
    $('kgMatrix').innerHTML=table(['??','????'], [['??',c.country_alpha3],['??',c.disease_slug],['??',c.metric_code],['??',c.cadence],['period_start',c.period_start],['period_end',c.period_end],['observed_count',c.observed_count],['nonzero_count',c.nonzero_count],['hard_gates',c.hard_gates]].map(r=>r.map(esc)));
    $('kgEvidence').innerHTML=table(['Evidence point','??','??','??','???','locator'], ev.map(x=>[esc(x.point_id),esc(x.model_key),esc(x.disease_slug),esc(x.point_type),esc(x.short_source_excerpt),esc(JSON.stringify(x.locator||{}).slice(0,180))]));
    $('kgParams').innerHTML=table(['???','?????','??','??'], [[esc(c.bundle_id),esc(c.parameter_confidence_score),'PAPER_EXACT / PAPER_RANGE / REGISTRY_DEFAULT / DATA_DOMAIN_PRIOR','DEFERRED_TO_R3B']]);
    $('kgExcluded').innerHTML=table(['??','??'], [['Baseline','BASELINE_FORBIDDEN_BY_POLICY??????????? top candidate'],['????','preexecution_evidence_score <= 70 ? parameter_confidence_score <= 70'],['metadata-only','parameter_evidence_count=0?score_contribution=0'],['quarantined / identity conflict','recommendation_eligible=false']].map(r=>r.map(esc)));
    $('kgDeferred').innerHTML=table(['?????','?'], Object.entries({actually_executed:c.actually_executed,fit_called:c.fit_called,predict_called:c.predict_called,local_backtest_status:c.local_backtest_status,runtime_validation_status:c.runtime_validation_status,forecast_run_id:c.forecast_run_id,forecast_value_count:c.forecast_value_count,local_metric_count:c.local_metric_count,formal_recommendation_eligible:c.formal_recommendation_eligible,promotion_eligible:c.promotion_eligible,active_pointer_eligible:c.active_pointer_eligible,accuracy_claim_allowed:c.accuracy_claim_allowed}).map(([k,v])=>[esc(k),esc(v)]));
    drawGraph(c, comps, ev);
  }
  function drawGraph(c, comps, ev){
    const svg=$('kgGraph'); svg.innerHTML=''; svg.setAttribute('viewBox','0 0 960 360'); const ns='http://www.w3.org/2000/svg';
    const nodes=[['DATA_PROFILE',c.profile_id],['MODEL_CONFIGURATION_CANDIDATE',c.candidate_id],['MODEL_BLUEPRINT',c.blueprint_id],['PARAMETER_BUNDLE_SPEC',c.bundle_id],['SCORE_COMPONENT',`${comps.length} components`],['EVIDENCE_POINT',`${ev.length} points`]];
    nodes.forEach((n,i)=>{const x=80+i*165,y=170; const g=document.createElementNS(ns,'g'); const rect=document.createElementNS(ns,'rect'); rect.setAttribute('x',x-55);rect.setAttribute('y',y-35);rect.setAttribute('width',120);rect.setAttribute('height',70);rect.setAttribute('rx',12);rect.setAttribute('class','kg-node-box'); const t=document.createElementNS(ns,'text'); t.setAttribute('x',x);t.setAttribute('y',y-5);t.setAttribute('text-anchor','middle');t.textContent=n[0]; const s=document.createElementNS(ns,'text');s.setAttribute('x',x);s.setAttribute('y',y+17);s.setAttribute('text-anchor','middle');s.setAttribute('class','kg-small');s.textContent=String(n[1]).slice(0,18); g.append(rect,t,s); svg.append(g); if(i){const line=document.createElementNS(ns,'line'); line.setAttribute('x1',x-155+65);line.setAttribute('y1',y);line.setAttribute('x2',x-55);line.setAttribute('y2',y);line.setAttribute('class','kg-edge');svg.append(line);}});
    $('kgTableFallback').innerHTML=table(['?','??'], [['DATA_PROFILE ? MATCHED_TO ? MODEL_CONFIGURATION_CANDIDATE','????????'],['MODEL_CONFIGURATION_CANDIDATE ? USES_BLUEPRINT','??????????'],['MODEL_CONFIGURATION_CANDIDATE ? HAS_SCORE_COMPONENT','A?F ????'],['SCORE_COMPONENT ? SUPPORTED_BY/LIMITED_BY ? EVIDENCE_POINT','???????']].map(r=>r.map(esc)));
  }
  $('kgRun').addEventListener('click',loadCandidates);
  loadCatalog().then(loadCandidates).catch(e=>{$('kgStatus').textContent='???'+e.message;});
})();
