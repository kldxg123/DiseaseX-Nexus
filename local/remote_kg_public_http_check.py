from __future__ import annotations

import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8020"
REPORT_DIR = Path("USER_HOME/projects/openclaw/reports/forecast_model_kg_v1_20260729T053519Z_nogit")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_url(path: str) -> tuple[int, str]:
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(BASE + path, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> None:
    checks = [
        "/healthz",
        "/readyz",
        "/login",
        "/forecast-model-knowledge-graph",
        "/static/forecast-model-kg.js",
        "/static/forecast-model-kg.css",
        "/api/v1/forecast-model-knowledge-graph",
        "/api/v1/forecast-model-knowledge-graph/Legionellosis",
        "/api/v1/forecast-model-guidance?disease=Legionellosis&country_or_region=AUT&target=cases_new&horizon=4",
    ]
    result: dict[str, object] = {}
    for path in checks:
        code, body = open_url(path)
        result[path] = {
            "status": code,
            "bytes": len(body),
            "has_bad_question_marks": "????" in body,
            "body_prefix": body[:120],
        }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = REPORT_DIR / "PUBLIC_HTTP_ROUTE_CHECK.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(output)
    assert result["/healthz"]["status"] == 200
    assert result["/readyz"]["status"] == 200
    assert result["/login"]["status"] == 200
    assert result["/forecast-model-knowledge-graph"]["status"] in {302, 303, 307}
    assert result["/static/forecast-model-kg.js"]["status"] == 200
    assert result["/static/forecast-model-kg.css"]["status"] == 200
    for path in checks[-3:]:
        assert result[path]["status"] in {401, 403}, (path, result[path]["status"])
    assert not any(item["has_bad_question_marks"] for item in result.values())


if __name__ == "__main__":
    main()
