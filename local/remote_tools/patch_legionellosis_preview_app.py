from __future__ import annotations

from pathlib import Path


APP = Path("USER_HOME/projects/openclaw/src/epidemic_core/dashboard/app.py")

STATIC_ROUTES = '''

    @app.get("/static/dashboard.legionellosis-preview.css")
    def legionellosis_preview_css() -> FileResponse:
        return FileResponse(static_dir / "dashboard.legionellosis-preview.css", media_type="text/css")

    @app.get("/static/dashboard.legionellosis-preview.js")
    def legionellosis_preview_js() -> FileResponse:
        return FileResponse(static_dir / "dashboard.legionellosis-preview.js", media_type="application/javascript")
'''

PAGE_ROUTES = r'''

    @app.get("/legionellosis-preview")
    def legionellosis_preview_page(request: Request) -> Response:
        if not _authorized(request):
            return RedirectResponse("/login", 303)
        html = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>美国军团菌病 CDC 周度 Preview</title><link rel="stylesheet" href="/static/dashboard.legionellosis-preview.css"></head><body class="legionellosis-preview"><header><div><h1>美国 Legionellosis / 军团菌病周度 Preview</h1><p>CDC NNDSS Weekly Tables · Current week · preview/staging only</p><p><a href="/">返回主 Dashboard</a> · <a id="csvLink" href="/api/v1/legionellosis-preview.csv">下载 CSV</a></p></div><form method="post" action="/logout"><button type="submit">退出</button></form></header><main><section id="notice" class="warning">预览数据：仅展示已验证周，尚未形成完整104周序列。</section><section id="status" class="notice" aria-live="polite">初始化…</section><section id="summary" class="summary" aria-label="覆盖摘要"></section><section class="card"><h2>周度 Current week 病例折线图</h2><p>缺失周和待验证周显示为缺口或底部标记；禁止补零和插值。</p><svg id="chart" role="img" aria-label="美国军团菌病 Current week preview 折线图"></svg></section><section id="gaps" class="card"></section><section class="card"><h2>已验证周数据表</h2><div id="table"></div></section></main><script src="/static/dashboard.legionellosis-preview.js" defer></script></body></html>"""
        return HTMLResponse(html)

    def _legionellosis_preview_payload_path() -> Path:
        return Path(__file__).resolve().parents[3] / "storage" / "preview" / "us_legionellosis" / "preview_us_legionellosis_weekly_current_week_v1.json"

    @app.get("/api/v1/legionellosis-preview")
    def legionellosis_preview_api(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        payload_path = _legionellosis_preview_payload_path()
        if not payload_path.exists():
            return _json_response({"detail": "LEGIONELLOSIS_PREVIEW_NOT_GENERATED"}, 404)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        return _json_response(payload)

    @app.get("/api/v1/legionellosis-preview.csv")
    def legionellosis_preview_csv(request: Request) -> Response:
        if not _authorized(request):
            return _unauthorized()
        csv_path = _legionellosis_preview_payload_path().with_suffix(".csv")
        if not csv_path.exists():
            return _json_response({"detail": "LEGIONELLOSIS_PREVIEW_NOT_GENERATED"}, 404)
        return FileResponse(csv_path, media_type="text/csv", filename="preview_us_legionellosis_weekly_current_week_v1.csv")
'''


def main() -> None:
    text = APP.read_text(encoding="utf-8")
    if "legionellosis_preview_page" in text:
        print("already patched")
        return
    text = text.replace(
        '    @app.get("/login")\n',
        STATIC_ROUTES + '\n    @app.get("/login")\n',
        1,
    )
    text = text.replace(
        "    install_preview_routes(app, engine, _authorized, _unauthorized, _query, _json_response)\n",
        PAGE_ROUTES + "\n    install_preview_routes(app, engine, _authorized, _unauthorized, _query, _json_response)\n",
        1,
    )
    APP.write_text(text, encoding="utf-8")
    print("patched")


if __name__ == "__main__":
    main()
