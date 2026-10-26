from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.config import SEARCH_PROVIDER_ENV, get_config


PROVIDER_ORDER = ("brave", "bing", "serpapi", "tavily", "exa")


def check_readiness(
    *,
    output_dir: str = "reports/deepseek_web_search",
    requested_provider: str = "auto",
) -> dict[str, Any]:
    cfg = get_config()
    requested = str(requested_provider or "auto").strip().lower()
    available = [provider for provider in PROVIDER_ORDER if bool(cfg.provider_api_keys.get(provider))]
    missing = [
        {"provider": provider, "env_var": SEARCH_PROVIDER_ENV[provider]}
        for provider in PROVIDER_ORDER
        if not cfg.provider_api_keys.get(provider)
    ]
    selected = _select_provider(
        requested=requested,
        configured=cfg.general_web_search_provider,
        available=available,
    )
    summary = {
        "status": "ready" if selected != "mock" else "missing_provider_key",
        "available_providers": available,
        "selected_provider": selected,
        "configured_provider": cfg.general_web_search_provider,
        "requested_provider": requested,
        "missing_provider_keys": missing,
        "can_run_live_web_search": selected != "mock",
        "deepseek_api_key_available": bool(cfg.has_deepseek_key),
        "safety_notes": [
            "Provider key values were not read into reports or printed.",
            "Readiness does not call any provider API.",
            "Search result URLs must not be opened automatically by readiness.",
        ],
    }
    _write_reports(summary, Path(output_dir))
    return summary


def _select_provider(*, requested: str, configured: str, available: list[str]) -> str:
    if requested == "mock":
        return "mock"
    if requested in PROVIDER_ORDER:
        return requested if requested in available else "mock"
    configured = str(configured or "").strip().lower()
    if configured in PROVIDER_ORDER and configured in available:
        return configured
    return available[0] if available else "mock"


def _write_reports(summary: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "GENERAL_SEARCH_PROVIDER_READINESS.json"
    md_path = output_dir / "GENERAL_SEARCH_PROVIDER_READINESS.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# General Search Provider Readiness",
        "",
        f"Status: `{summary['status']}`",
        f"Selected provider: `{summary['selected_provider']}`",
        f"Available providers: `{', '.join(summary['available_providers']) or 'none'}`",
        f"Can run live web search: `{summary['can_run_live_web_search']}`",
        "",
        "No provider key values are printed or stored in this report.",
    ]
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check external general web search provider readiness.")
    parser.add_argument("--output-dir", default="reports/deepseek_web_search")
    parser.add_argument("--requested-provider", "--search-backend", default="auto")
    args = parser.parse_args(argv)
    summary = check_readiness(output_dir=args.output_dir, requested_provider=args.requested_provider)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "available_providers": summary["available_providers"],
                "selected_provider": summary["selected_provider"],
                "can_run_live_web_search": summary["can_run_live_web_search"],
                "readiness_json": str(Path(args.output_dir) / "GENERAL_SEARCH_PROVIDER_READINESS.json"),
                "readiness_markdown": str(Path(args.output_dir) / "GENERAL_SEARCH_PROVIDER_READINESS.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
