from __future__ import annotations

from src.data_acquisition.download_orchestrator import download_mobility_sources, main


__all__ = ["download_mobility_sources", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
