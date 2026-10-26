# epidemic-openclaw

Local-first infectious-disease intelligence platform scaffold. The current stage is AutoDL-P0-1 baseline initialization on an Ubuntu 22.04 / Python 3.10 / CUDA 11.8-labelled image; actual versions are recorded from server probes. The project and its Conda environment live on the AutoDL data disk.

Project environment: `USER_HOME/autodl-tmp/conda/envs/epidemic-core`. OpenClaw, databases, and Qwen are not installed or deployed in this stage. Important data requires an external backup.


## PostgreSQL 18.4

PostgreSQL is deployed from conda-forge in `USER_HOME/autodl-tmp/conda/envs/postgresql18` and stores data in `USER_HOME/autodl-tmp/epidemic-openclaw/storage/database/postgresql18/data`. Use `scripts/postgres/start.sh`, `stop.sh`, and `status.sh`. AutoDL instance startup does not automatically start PostgreSQL; run `start.sh` after each instance boot. Backups are stored in `USER_HOME/autodl-tmp/epidemic-openclaw/storage/database/postgresql18/backups`. The AutoDL data disk is not permanent reliable storage, so maintain external backups.


## Qwen3-8B-AWQ local inference

The official AWQ model is stored at `USER_HOME/autodl-tmp/epidemic-openclaw/models/llm/Qwen3-8B-AWQ` and served from the isolated `USER_HOME/autodl-tmp/conda/envs/qwen-vllm` environment. Use `scripts/qwen_vllm/start.sh`, `stop.sh`, and `status.sh`; the local API is `http://127.0.0.1:8000/v1`. Restart the service manually after an AutoDL instance boot. OpenClaw is not installed. This model supports research and agent orchestration and is not a direct numerical epidemic forecasting system.

## P0-4 OpenClaw local control plane

OpenClaw runs natively from the data disk through `scripts/openclaw/` and connects only to the existing loopback vLLM service. The Gateway is loopback-only with token authentication; bundled skills and plugins are disabled, and only `session_status` is exposed.

## P1-1 domain storage foundation

The `epidemic_core` package provides strict country, disease, and ISO-date normalization; SQLAlchemy repositories; content-addressed raw artifacts; Alembic migrations; immutable provenance/observation records; and deterministic dataset snapshots. Database migration and application DSNs are separate process environment secrets. No network API service or external source adapter is part of P1-1.

RLS is intentionally deferred until a real multi-tenant boundary exists. The application role remains `NOBYPASSRLS` and has no schema DDL permission.
