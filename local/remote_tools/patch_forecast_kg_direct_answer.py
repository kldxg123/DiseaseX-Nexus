from __future__ import annotations

from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")


def patch_js() -> None:
    js = ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js"
    text = js.read_text(encoding="utf-8")
    insert = r'''
  function directAnswer(data){
    const disease = data.resolved_disease || {};
    const profile = data.dataset_profile || {};
    const primary = (data.recommended_models || [])[0] || (data.recommended_model_families || [])[0] || {};
    const baselines = (data.required_baselines || []).map(x => x.label_zh || x.label_en || x.model_id).filter(Boolean).slice(0,3);
    const warnings = data.warnings || [];
    const country = profile.country_or_region || data.request?.country_or_region || '未指定国家/地区';
    const target = profile.forecast_target || data.request?.forecast_target || 'cases_new';
    const track = data.eligibility_track || 'UNKNOWN';
    const weeks = profile.history_weeks_normalized;
    const regions = profile.region_count;
    const quality = profile.data_quality_status || 'unknown';
    let because = `因为 ${esc(country)} 的 ${esc(disease.label_zh || disease.label_en || data.request?.disease)} 当前数据画像为 ${esc(track)}`;
    if (weeks !== null && weeks !== undefined) because += `，历史长度约 ${esc(weeks)} 周`;
    if (regions !== null && regions !== undefined) because += `，可用地区/序列数 ${esc(regions)}`;
    because += `，数据质量门禁为 ${esc(quality)}`;
    if (warnings.length) because += `，且存在限制：${esc(warnings[0])}`;
    let therefore;
    if (track === 'FORMAL') {
      therefore = `所以目前应采用 ${esc(primary.label_zh || primary.label_en || primary.model_id || '候选模型')} 与 ${esc(baselines.join('、') || '基线模型')} 做 rolling-origin 回测比较，只有 champion guard 通过后才发布 ${esc(target)} 预测。`;
    } else if (track === 'DEMO') {
      therefore = `所以目前只适合演示级预测：优先用 ${esc(primary.label_zh || primary.label_en || primary.model_id || '简单基线/轻量模型')}，必须标注 NOT_PRODUCTION_VALIDATED，不能当正式预测。`;
    } else if (track === 'SPARSE_OUTBREAK') {
      therefore = `所以目前不应伪装成连续周度预测，应采用事件级/场景级风险说明、最近观测或人工复核摘要，而不是补零训练模型。`;
    } else {
      therefore = `所以目前不应启动正式预测；应先补齐可信数据、覆盖率、最近性和地区数，再进入回测选型。`;
    }
    return `${because}；${therefore}`;
  }
'''
    if "function directAnswer(data)" not in text:
        text = text.replace("  function renderGraph(data) {", insert + "\n  function renderGraph(data) {")

    old = """      $('kgStatus').innerHTML=`<strong>${esc(data.resolved_disease.label_zh)} / ${esc(data.resolved_disease.label_en)}</strong> - Track: <span class=\"kg-pill\">${esc(data.eligibility_track)}</span> - Graph SHA: <code>${esc(data.graph_content_sha256.slice(0,12))}</code>`;"""
    new = """      $('kgStatus').innerHTML=`<strong>${esc(data.resolved_disease.label_zh)} / ${esc(data.resolved_disease.label_en)}</strong> - Track: <span class=\"kg-pill\">${esc(data.eligibility_track)}</span> - Graph SHA: <code>${esc(data.graph_content_sha256.slice(0,12))}</code>`;
      $('kgDirectAnswer').innerHTML=`<h2>直接结论 / Direct answer</h2><p>${directAnswer(data)}</p>`;"""
    if "$('kgDirectAnswer').innerHTML" not in text:
        if old not in text:
            raise RuntimeError("status assignment anchor not found")
        text = text.replace(old, new)

    old_catch = """    } catch(e) { $('kgStatus').innerHTML=`<span class=\"kg-error\">Guidance failed: ${esc(e.message)}</span>`; }"""
    new_catch = """    } catch(e) { $('kgStatus').innerHTML=`<span class=\"kg-error\">Guidance failed: ${esc(e.message)}</span>`; $('kgDirectAnswer').innerHTML='<h2>直接结论 / Direct answer</h2><p class=\"kg-error\">无法生成建议：请检查病种名称、国家/地区和登录状态。</p>'; }"""
    if "$('kgDirectAnswer').innerHTML='<h2>直接结论" not in text:
        text = text.replace(old_catch, new_catch)
    js.write_text(text, encoding="utf-8")


def patch_html() -> None:
    app = ROOT / "src/epidemic_core/forecast_model_kg/dashboard_app.py"
    html = app.read_text(encoding="utf-8")
    html = html.replace(
        '<input id="kgDisease" list="kgDiseases" value="Legionella">',
        '<input id="kgDisease" list="kgDiseases" value="Legionellosis">',
    )
    html = html.replace(
        '<input id="kgCountry" value="AUT" placeholder="FIN / AUT / BGD">',
        '<input id="kgCountry" value="United States" placeholder="United States / FIN / AUT / BGD">',
    )
    anchor = '''    <section id="kgStatus" class="kg-status" aria-live="polite">Loading guidance...</section>
    <section class="kg-grid">'''
    replacement = '''    <section id="kgStatus" class="kg-status" aria-live="polite">Loading guidance...</section>
    <section id="kgDirectAnswer" class="kg-direct-answer" aria-live="polite"><h2>直接结论 / Direct answer</h2><p>Loading guidance...</p></section>
    <section class="kg-grid">'''
    if 'id="kgDirectAnswer"' not in html:
        if anchor not in html:
            raise RuntimeError("html anchor not found")
        html = html.replace(anchor, replacement)
    app.write_text(html, encoding="utf-8")


def patch_css() -> None:
    css = ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.css"
    text = css.read_text(encoding="utf-8")
    if ".kg-direct-answer" not in text:
        text = text.replace(
            ".kg-warning,.kg-status,.kg-notes,.kg-grid article,.kg-controls",
            ".kg-warning,.kg-status,.kg-direct-answer,.kg-notes,.kg-grid article,.kg-controls",
        )
        text += "\n.kg-direct-answer{border-left:6px solid #0f766e;background:#f0fdfa}.kg-direct-answer h2{margin:.1rem 0 .5rem}.kg-direct-answer p{font-size:1.08rem;line-height:1.65;margin:.25rem 0;color:#123b3a}\n"
    css.write_text(text, encoding="utf-8")


def main() -> int:
    patch_js()
    patch_html()
    patch_css()
    print("patch=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
