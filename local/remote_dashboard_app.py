
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine

from .service import disease_graph_response, full_graph_response, guidance_response, metadata_response

KG_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>&#39044;&#27979;&#27169;&#22411;&#30693;&#35782;&#22270;&#35889; / Forecast Model Knowledge Graph</title>
  <link rel="stylesheet" href="/static/forecast-model-kg.css">
</head>
<body class="kg-page">
  <header>
    <div>
      <h1>&#39044;&#27979;&#27169;&#22411;&#30693;&#35782;&#22270;&#35889; / Forecast Model Knowledge Graph</h1>
      <p>&#25353;&#30149;&#31181;&#12289;&#20256;&#25773;&#26426;&#21046;&#12289;&#25968;&#25454;&#23494;&#24230;&#12289;&#39044;&#27979;&#30446;&#26631;&#12289;horizon &#21644;&#21327;&#21464;&#37327;&#26465;&#20214;&#32473;&#20986;&#27169;&#22411;&#20505;&#36873;&#12290;This page is guidance only: no training, no prediction run, no promotion.</p>
    </div>
    <nav>
      <a href="/">Dashboard</a>
      <a href="/predictions">Predictions</a>
      <a href="/fasttrack-forecast">Fasttrack</a>
    </nav>
    <form method="post" action="/logout"><button type="submit">Logout</button></form>
  </header>
  <main>
    <section class="kg-warning">
      <strong>&#38480;&#21046;&#35828;&#26126;</strong>
      &#26412;&#39029;&#19981;&#20195;&#26367; rolling-origin backtest&#12289;baseline guard &#25110;&#20154;&#24037;&#23457;&#26680;&#12290;&#27169;&#22411;&#36873;&#25321;&#24517;&#39035;&#30001;&#23454;&#38469;&#22238;&#27979;&#39564;&#35777;&#20915;&#23450;&#12290;
    </section>
    <section class="kg-controls" aria-label="Model guidance controls">
      <label>&#30149;&#31181; / Disease
        <input id="kgDisease" list="kgDiseases" value="Legionella">
        <datalist id="kgDiseases">
          <option>COVID-19</option><option>Influenza</option><option>Dengue</option><option>Ebola</option>
          <option>Puumala</option><option>Legionellosis</option><option>Nipah</option>
        </datalist>
      </label>
      <label>&#22269;&#23478;&#25110;&#22320;&#21306; / Country or region
        <input id="kgCountry" value="AUT" placeholder="FIN / AUT / BGD">
      </label>
      <label>&#30446;&#26631; / Target<input id="kgTarget" value="cases_new"></label>
      <label>Horizon weeks<input id="kgHorizon" type="number" min="1" max="52" value="4"></label>
      <button id="kgRun" type="button">&#29983;&#25104;&#24314;&#35758; / Generate guidance</button>
    </section>
    <section id="kgStatus" class="kg-status" aria-live="polite">Loading guidance...</section>
    <section class="kg-grid">
      <article><h2>1. Recommended model pool / &#25512;&#33616;&#27169;&#22411;&#27744;</h2><div id="kgModels"></div></article>
      <article><h2>2. Why / &#25512;&#33616;&#29702;&#30001;</h2><div id="kgReasons"></div></article>
      <article><h2>3. Validation requirements / &#39564;&#35777;&#35201;&#27714;</h2><div id="kgValidation"></div></article>
      <article><h2>4. Graph and table / &#22270;&#35889;&#19982;&#34920;&#26684;</h2><svg id="kgGraph" role="img" aria-label="Forecast model knowledge graph"></svg><div id="kgTableFallback"></div></article>
    </section>
    <section class="kg-notes">
      <h2>&#30149;&#31181;&#36793;&#30028;&#25552;&#37266; / Disease boundary reminders</h2>
      <ul>
        <li>Finland Puumala guidance must not be generalized to all Hantavirus settings without ecological evidence.</li>
        <li>Legionella guidance requires a concrete country; Europe is not treated as a pseudo-country.</li>
        <li>Nipah guidance treats unreported periods as unknown sparse events; it must not fabricate zero-filled weekly series.</li>
      </ul>
    </section>
  </main>
  <script src="/static/forecast-model-kg.js" defer></script>
</body>
</html>"""


def install_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    json_response: Callable[[Any, int], Response],
) -> None:
    static_dir = Path(__file__).resolve().parents[1] / "dashboard" / "static"

    @app.get("/static/forecast-model-kg.css")
    def forecast_model_kg_css() -> FileResponse:
        return FileResponse(static_dir / "forecast-model-kg.css", media_type="text/css")

    @app.get("/static/forecast-model-kg.js")
    def forecast_model_kg_js() -> FileResponse:
        return FileResponse(static_dir / "forecast-model-kg.js", media_type="application/javascript")

    @app.get("/forecast-model-knowledge-graph")
    def forecast_model_kg_page(request: Request) -> Response:
        return HTMLResponse(KG_HTML) if authorized(request) else RedirectResponse("/login", 303)

    @app.get("/api/v1/forecast-model-knowledge-graph")
    def forecast_model_kg_api(request: Request, full: bool = False) -> Response:
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(full_graph_response() if full else metadata_response(), 200)
        except Exception:
            return json_response({"detail": "FORECAST_MODEL_KG_LOAD_FAILED"}, 503)

    @app.get("/api/v1/forecast-model-knowledge-graph/{disease_id}")
    def forecast_model_kg_disease_api(request: Request, disease_id: str) -> Response:
        if not authorized(request):
            return unauthorized()
        try:
            return json_response(disease_graph_response(disease_id), 200)
        except ValueError:
            return json_response({"detail": "UNSUPPORTED_DISEASE"}, 404)
        except Exception:
            return json_response({"detail": "FORECAST_MODEL_KG_LOAD_FAILED"}, 503)

    @app.get("/api/v1/forecast-model-guidance")
    def forecast_model_guidance_api(
        request: Request,
        disease: str,
        country_or_region: str | None = None,
        target: str | None = "cases_new",
        horizon: int | None = 4,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if horizon is not None and not 1 <= horizon <= 104:
            return json_response({"detail": "INVALID_HORIZON"}, 422)
        try:
            return json_response(guidance_response(engine, disease, country_or_region, target, horizon), 200)
        except ValueError as exc:
            code = str(exc) or "INVALID_REQUEST"
            return json_response({"detail": code}, 404 if code == "UNSUPPORTED_DISEASE" else 422)
        except Exception:
            return json_response({"detail": "FORECAST_MODEL_GUIDANCE_FAILED"}, 503)
