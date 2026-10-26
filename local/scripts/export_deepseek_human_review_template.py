from __future__ import annotations

import argparse
import json

from deepseek_web_search_agent.human_review_promotion import export_human_review_template


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a safe DeepSeek human review annotation template.")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = export_human_review_template(args.report_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
