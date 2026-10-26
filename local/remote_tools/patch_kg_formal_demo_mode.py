from __future__ import annotations

from pathlib import Path


ROOT = Path("USER_HOME/projects/openclaw")


def patch_dashboard_app() -> None:
    p = ROOT / "src/epidemic_core/forecast_model_kg/dashboard_app.py"
    text = p.read_text(encoding="utf-8")
    old_sig = """        disease: str,
        country_or_region: str | None = None,
        target: str | None = "cases_new",
        horizon: int | None = 4,
    ) -> Response:
"""
    new_sig = """        disease: str,
        country_or_region: str | None = None,
        target: str | None = "cases_new",
        horizon: int | None = 4,
        demo_profile: str | None = None,
    ) -> Response:
"""
    if "demo_profile: str | None = None" not in text:
        if old_sig not in text:
            raise RuntimeError("route signature anchor not found")
        text = text.replace(old_sig, new_sig)

    old_body = """        try:
            return json_response(guidance_response(engine, disease, country_or_region, target, horizon), 200)
"""
    new_body = """        try:
            explicit_profile = None
            if demo_profile:
                if demo_profile != "formal_sufficient":
                    return json_response({"detail": "UNSUPPORTED_DEMO_PROFILE"}, 422)
                explicit_profile = {
                    "disease_id": None,
                    "country_or_region": country_or_region,
                    "forecast_target": target or "cases_new",
                    "horizon": horizon,
                    "time_granularity": "weekly",
                    "history_weeks_normalized": 156,
                    "period_count": 156,
                    "region_count": 8,
                    "nonzero_period_count": 64,
                    "zero_ratio": 0.2,
                    "is_event_series": False,
                    "is_continuous_surveillance": True,
                    "data_quality_status": "PASS",
                    "source_ids": ("demo-formal-sufficient-profile",),
                    "metric_fallback_used": False,
                }
            return json_response(guidance_response(engine, disease, country_or_region, target, horizon, explicit_profile=explicit_profile), 200)
"""
    if "UNSUPPORTED_DEMO_PROFILE" not in text:
        if old_body not in text:
            raise RuntimeError("route body anchor not found")
        text = text.replace(old_body, new_body)

    old_input = '<label>Horizon weeks<input id="kgHorizon" type="number" min="1" max="52" value="4"></label>\n      <button id="kgRun" type="button">'
    new_input = '<label>Horizon weeks<input id="kgHorizon" type="number" min="1" max="52" value="4"></label>\n      <label>演示画像 / Demo profile\n        <select id="kgDemoProfile"><option value="">Use current database</option><option value="formal_sufficient">Data sufficient / FORMAL demo</option></select>\n      </label>\n      <button id="kgRun" type="button">'
    if 'id="kgDemoProfile"' not in text:
        if old_input not in text:
            raise RuntimeError("html demo select anchor not found")
        text = text.replace(old_input, new_input)

    p.write_text(text, encoding="utf-8")


def patch_js() -> None:
    p = ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js"
    text = p.read_text(encoding="utf-8")
    old = "const q = new URLSearchParams({disease:$('kgDisease').value, country_or_region:$('kgCountry').value, target:$('kgTarget').value, horizon:$('kgHorizon').value});"
    new = """const q = new URLSearchParams({disease:$('kgDisease').value, country_or_region:$('kgCountry').value, target:$('kgTarget').value, horizon:$('kgHorizon').value});
    if ($('kgDemoProfile') && $('kgDemoProfile').value) q.set('demo_profile', $('kgDemoProfile').value);"""
    if "demo_profile" not in text:
        if old not in text:
            raise RuntimeError("js query anchor not found")
        text = text.replace(old, new)

    old_because = "let because = `因为 ${esc(country)} 的 ${esc(disease.label_zh || disease.label_en || data.request?.disease)} 当前数据画像为 ${esc(track)}`;"
    new_because = """let because = `因为 ${esc(country)} 的 ${esc(disease.label_zh || disease.label_en || data.request?.disease)} 当前数据画像为 ${esc(track)}`;
    if (data.dataset_profile_source === 'user_supplied_profile') because += '（演示画像，不代表当前数据库真实入库状态）';"""
    if "演示画像，不代表当前数据库真实入库状态" not in text:
        if old_because not in text:
            raise RuntimeError("direct answer source anchor not found")
        text = text.replace(old_because, new_because)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    patch_dashboard_app()
    patch_js()
    print("patch=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
