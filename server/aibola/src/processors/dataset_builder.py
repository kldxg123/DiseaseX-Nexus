from __future__ import annotations

import argparse

from src.processors.label_builder import main as label_main


def main() -> int:
    parser = argparse.ArgumentParser(description="Build modeling datasets.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--target", default="qwen25")
    args = parser.parse_args()
    import sys

    sys.argv = ["label_builder", "--config", args.config, "--horizon-days", "14", "--target", args.target]
    return label_main()


if __name__ == "__main__":
    raise SystemExit(main())
