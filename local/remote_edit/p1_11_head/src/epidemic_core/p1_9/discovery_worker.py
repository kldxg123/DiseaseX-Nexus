from __future__ import annotations

import datetime as dt
import os
import signal
import time
import uuid

from sqlalchemy import create_engine

from epidemic_core.discovery.safe_fetcher import SafeFetcher
from epidemic_core.discovery.worker import POLL_SECONDS, Queue, process_claim


def _candidate_timeout() -> float:
    requested = float(os.environ.get("P1_9_CANDIDATE_FETCH_TIMEOUT_SECONDS", "90"))
    return min(120.0, max(30.0, requested))


def main() -> None:
    if os.environ.get("APP_ENV") != "p1_9_demo":
        raise SystemExit("P1_9_DISCOVERY_WORKER_DEMO_ONLY")
    engine = create_engine(os.environ["APP_DATABASE_URL"], pool_pre_ping=True, hide_parameters=True)
    started_at = dt.datetime.now(dt.timezone.utc)
    worker_id = f"p1-9-discovery-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    queue = Queue(engine, worker_id, started_at)
    candidate_fetcher = SafeFetcher(maximum_bytes=50 * 1024 * 1024, timeout_seconds=_candidate_timeout())
    stopped = False

    def halt(*_: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    last_error: str | None = None
    while not stopped:
        try:
            queue.touch(error=last_error)
            claim = queue.claim()
            if claim is None:
                last_error = None
                time.sleep(POLL_SECONDS)
                continue
            queue.touch(current=claim.run_id)
            try:
                process_claim(queue, claim, candidate_fetcher=candidate_fetcher)
                last_error = None
            except Exception as exc:
                last_error = type(exc).__name__
                try:
                    if claim.attempt < 3:
                        queue.finish(
                            claim,
                            "FAILED_RETRYABLE",
                            "WORKER_TASK_EXCEPTION",
                            {"message": type(exc).__name__},
                            {"blocked": 1},
                            retry_seconds=min(60, 2**claim.attempt),
                        )
                    else:
                        queue.finish(
                            claim,
                            "FAILED_TERMINAL",
                            "WORKER_TASK_RETRY_EXHAUSTED",
                            {"message": type(exc).__name__},
                            {"blocked": 1},
                        )
                except Exception:
                    engine.dispose()
            finally:
                queue.touch(error=last_error)
        except Exception as exc:
            last_error = type(exc).__name__
            engine.dispose()
            time.sleep(min(5.0, POLL_SECONDS))
    engine.dispose()


if __name__ == "__main__":
    main()
