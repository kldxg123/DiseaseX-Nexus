#!/usr/bin/env bash
set -euo pipefail
source USER_HOME/autodl-tmp/epidemic-openclaw/scripts/retrieval_worker/retrieval_worker_env.sh
"$PYTHON" -B -c 'from pathlib import Path;from epidemic_core.config import Settings;from epidemic_core.db.engine import create_app_engine;from epidemic_core.db.session import session_factory;from epidemic_core.retrieval.worker import RetrievalWorker;s=Settings();e=create_app_engine(s);w=RetrievalWorker(session_factory(e),Path("USER_HOME/autodl-tmp/epidemic-openclaw/reference/data_sources.v1.json"),s.RAW_ARTIFACT_ROOT);raise SystemExit(0 if w.run_once() else 3)'
