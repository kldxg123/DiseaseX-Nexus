
(function(){
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function card(item, cls='') { return `<div class="kg-card ${cls}"><h3>${esc(item.label_zh || item.model_id)} <small>${esc(item.label_en || '')}</small></h3><p>${esc(item.reason || '')}</p>${(item.conditions_missing||[]).length ? `<p><strong>Missing requirements: </strong>${esc(item.conditions_missing.join(', '))}</p>` : ''}</div>`; }
  function renderList(title, items, cls='') { if(!items || !items.length) return `<p>None.</p>`; return items.map(i => card(i, cls)).join(''); }
  function renderGraph(data) {
    const svg=$('kgGraph'); svg.innerHTML=''; const ns='http://www.w3.org/2000/svg';
    const nodes=[data.resolved_disease, ...(data.recommended_model_families||[]).slice(0,5).map(m=>({id:m.model_id,label_zh:m.label_zh,label_en:m.label_en,type:'model'})), ...(data.required_validation||[]).slice(0,3).map(v=>({id:v.id,label_zh:v.label_zh,label_en:v.label_en,type:'validation'})), ...(data.evidence||[]).slice(0,3).map(e=>({id:e.id,label_zh:e.title,label_en:e.evidence_type,type:'evidence'}))];
    const coords=[]; const w=720,h=300; svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
    nodes.forEach((n,i)=>{ const angle=(Math.PI*2*i/Math.max(nodes.length,1))-Math.PI/2; const r=i===0?0:115; coords.push({x:w/2+Math.cos(angle)*r,y:h/2+Math.sin(angle)*r,n}); });
    coords.slice(1).forEach(c=>{ const line=document.createElementNS(ns,'line'); line.setAttribute('x1',coords[0].x); line.setAttribute('y1',coords[0].y); line.setAttribute('x2',c.x); line.setAttribute('y2',c.y); line.setAttribute('class','kg-edge'); svg.appendChild(line); });
    coords.forEach(c=>{ const g=document.createElementNS(ns,'g'); const circle=document.createElementNS(ns,'circle'); circle.setAttribute('cx',c.x); circle.setAttribute('cy',c.y); circle.setAttribute('r', c===coords[0]?42:34); circle.setAttribute('class','kg-node '+(c.n.type||'')); const text=document.createElementNS(ns,'text'); text.setAttribute('x',c.x); text.setAttribute('y',c.y+4); text.setAttribute('text-anchor','middle'); text.setAttribute('class','kg-label'); text.textContent=(c.n.label_zh||c.n.id||'').slice(0,12); g.appendChild(circle); g.appendChild(text); svg.appendChild(g); });
  }
  async function run(){
    const q = new URLSearchParams({disease:$('kgDisease').value, country_or_region:$('kgCountry').value, target:$('kgTarget').value, horizon:$('kgHorizon').value});
    $('kgStatus').textContent='Loading guidance...';
    try { const r=await fetch('/api/v1/forecast-model-guidance?'+q.toString(),{credentials:'same-origin'}); const data=await r.json(); if(!r.ok) throw new Error(data.detail||r.status);
      $('kgStatus').innerHTML=`<strong>${esc(data.resolved_disease.label_zh)} / ${esc(data.resolved_disease.label_en)}</strong> - Track: <span class="kg-pill">${esc(data.eligibility_track)}</span> - Graph SHA: <code>${esc(data.graph_content_sha256.slice(0,12))}</code>`;
      $('kgModels').innerHTML='<h3>Primary</h3>'+renderList('',data.recommended_models)+'<h3>Secondary / Conditional</h3>'+renderList('',data.conditional_models,'conditional')+'<h3>Avoid as primary</h3>'+renderList('',data.models_not_recommended_as_primary,'avoid');
      $('kgReasons').innerHTML=(data.reasons||[]).map(x=>`<p>${esc(x)}</p>`).join('')+(data.warnings||[]).map(x=>`<p class="kg-error">Warning: ${esc(x)}</p>`).join('')+`<table class="kg-table"><tr><th>Profile field</th><th>Value</th></tr>${Object.entries(data.dataset_profile||{}).slice(0,18).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v===null?'unknown / not observed':Array.isArray(v)?v.join(', '):v)}</td></tr>`).join('')}</table>`;
      $('kgValidation').innerHTML='<h3>Required baselines</h3>'+renderList('',data.required_baselines)+'<h3>Validation</h3><ul>'+(data.required_validation||[]).map(v=>`<li>${esc(v.label_zh)} / ${esc(v.label_en)}</li>`).join('')+'</ul><h3>Evidence</h3><ul>'+(data.evidence||[]).map(e=>`<li><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a> <small>${esc(e.evidence_type)}</small></li>`).join('')+'</ul>';
      renderGraph(data);
      $('kgTableFallback').innerHTML='<table class="kg-table"><tr><th>Rule</th><th>Priority</th><th>When</th></tr>'+(data.decision_trace||[]).map(t=>`<tr><td>${esc(t.rule_id)}</td><td>${esc(t.priority)}</td><td>${esc(JSON.stringify(t.when))}</td></tr>`).join('')+'</table>';
    } catch(e) { $('kgStatus').innerHTML=`<span class="kg-error">Guidance failed: ${esc(e.message)}</span>`; }
  }
  document.addEventListener('DOMContentLoaded',()=>{ $('kgRun').addEventListener('click',run); run(); });
})();
