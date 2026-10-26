from __future__ import annotations

import logging
from pathlib import Path

from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.session import session_factory
from epidemic_core.retrieval.worker import RetrievalWorker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    engine = create_app_engine(settings)
    try:
        RetrievalWorker(
            session_factory(engine),
            Path("USER_HOME/autodl-tmp/epidemic-openclaw/reference/data_sources.v1.json"),
            settings.RAW_ARTIFACT_ROOT,
        ).serve()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
