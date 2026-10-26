from __future__ import annotations

import argparse
import json

from src.remote.p0c1_runner import P0c1Stage, run_stage


def main() -> int:
    args = argparse.ArgumentParser(description="Check remote HF/hf-mirror connectivity for P0c-1.")
    args.add_argument("--root", default=".")
    args.add_argument("--dry-run", action="store_true")
    ns = args.parse_args()
    return run_stage(
        P0c1Stage(
            "check_hf_mirror",
            "python -m src.remote.hf_connectivity_probe",
            timeout=300,
        ),
        ns.root,
        ns.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
