from __future__ import annotations

import argparse
import json

from deepseek_web_search_agent.who_downloadable_targeter import run_who_data_acquisition, safe_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run WHO-first Ebola downloadable/tabular data acquisition test.")
    parser.add_argument("--disease", default="Ebola")
    parser.add_argument("--country-or-region", default="Democratic Republic of the Congo")
    parser.add_argument("--date-start", default="2025-01-01")
    parser.add_argument("--date-end", default="2025-12-31")
    parser.add_argument("--provider", default="tavily")
    parser.add_argument("--output-dir", default="reports/who_data_acquisition")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-live-who", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--max-provider-requests", type=int, default=3)
    parser.add_argument("--max-metadata-checks", type=int, default=3)
    args = parser.parse_args(argv)
    dry_run = bool(args.dry_run or not args.allow_live_who)
    result = run_who_data_acquisition(
        disease=args.disease,
        country_or_region=args.country_or_region,
        date_start=args.date_start,
        date_end=args.date_end,
        provider=args.provider,
        output_dir=args.output_dir,
        dry_run=dry_run,
        allow_live_who=bool(args.allow_live_who),
        allow_network=bool(args.allow_network),
        terms_reviewed=bool(args.terms_reviewed),
        manual_reviewed=bool(args.manual_reviewed),
        max_provider_requests=args.max_provider_requests,
        max_metadata_checks=args.max_metadata_checks,
    )
    print(json.dumps(safe_summary(result.to_dict()), ensure_ascii=False, sort_keys=True))
    return 0 if result.status in {"ok", "no_candidates"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
