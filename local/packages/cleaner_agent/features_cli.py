from __future__ import annotations

import argparse
import json

from cleaner_agent.features import FeatureBuilder
from common.config import get_settings
from common.db import get_session, init_db


def _parse_horizons(value: str) -> tuple[int, ...]:
    try:
        horizons = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("horizons-days must be comma-separated integers.") from exc
    if not horizons:
        raise argparse.ArgumentTypeError("horizons-days must include at least one value.")
    for horizon in horizons:
        if horizon <= 0 or horizon % 7 != 0:
            raise argparse.ArgumentTypeError("horizons-days values must be positive multiples of 7.")
    return horizons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local model_features from epidemic_events.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--country", default=None)
    parser.add_argument("--disease", default=None)
    parser.add_argument("--admin1", default=None)
    parser.add_argument("--horizons-days", type=_parse_horizons, default=(7, 28))
    parser.add_argument("--outbreak-threshold", type=int, default=3)
    parser.add_argument("--split-type", default="train")
    parser.add_argument("--no-replace-existing", action="store_true")
    args = parser.parse_args(argv)

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        summary = FeatureBuilder(session).build(
            country=args.country,
            disease=args.disease,
            admin1=args.admin1,
            horizons_days=args.horizons_days,
            outbreak_threshold=args.outbreak_threshold,
            split_type=args.split_type,
            replace_existing=not args.no_replace_existing,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
