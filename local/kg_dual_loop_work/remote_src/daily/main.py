from __future__ import annotations

import logging
from pathlib import Path

from epidemic_core.config import Settings
from epidemic_core.daily.worker import DailyUpdateCoordinator
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.session import session_factory
from epidemic_core.ebola.service import EbolaMonitoringService
from epidemic_core.p1_12.trusted import TrustedUpdateService, create_trusted_ingest_engine


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    engine = create_app_engine(settings)
    trusted_engine = create_trusted_ingest_engine()
    try:
        project_root = Path(__file__).resolve().parents[3]
        ebola_monitor = EbolaMonitoringService(engine, project_root)
        trusted_monitor = TrustedUpdateService(engine, project_root, trusted_engine)
        trusted_monitor.seed_due("startup")

        def kg_shadow_reconcile_tick() -> bool:
            """Controlled post-step: reconcile matured actuals into KG shadow
            objective feedback. Reads official observations only; any failure
            is logged and never breaks the daily update loop."""
            try:
                from epidemic_core.forecast_model_kg.dual_loop import reconcile_feedback

                raw = engine.raw_connection()
                try:
                    result = reconcile_feedback(raw)
                    raw.commit()
                    if result.get("newly_matured"):
                        logging.getLogger(__name__).info("kg shadow feedback reconciled: %s", result)
                    return bool(result.get("newly_matured"))
                finally:
                    raw.close()
            except Exception:  # noqa: BLE001 - post-step must never break daily updates
                logging.getLogger(__name__).exception("kg shadow reconcile tick failed")
                return False

        def controlled_official_tick() -> bool:
            changed = trusted_monitor.run_due_once() or ebola_monitor.run_due_once()
            return kg_shadow_reconcile_tick() or changed

        DailyUpdateCoordinator(
            session_factory(engine),
            project_root / "reference/data_sources.v2.json",
            extra_tick=controlled_official_tick,
        ).serve()
    finally:
        trusted_engine.dispose()
        engine.dispose()


if __name__ == "__main__":
    main()
