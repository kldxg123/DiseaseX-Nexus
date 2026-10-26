from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()
DATA_ROOT = PROJECT.parent
PG_BIN = DATA_ROOT / "conda" / "envs" / "postgresql18" / "bin"
RUN_ID = (
    "epidemic_openclaw_portable_no_models_"
    + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    + "_"
    + secrets.token_hex(4)
)
OUT_ROOT = DATA_ROOT / "epidemic-openclaw-evidence" / RUN_ID
PAYLOAD = OUT_ROOT / "payload"
DOCS = PAYLOAD / "docs"
SCRIPTS = PAYLOAD / "scripts"
DATABASE = PAYLOAD / "database"

EXCLUDES = [
    "./.git",
    "./.secrets",
    "./.env",
    "./.env.*",
    "./private",
    "./models",
    "./node_modules",
    "./.venv",
    "./venv",
    "./runtime",
    "./reports",
    "./backups",
    "./artifacts",
    "./storage/cache",
    "./storage/database",
    "./storage/database_snapshots",
    "./storage/database_backups",
    "./storage/secrets",
    "./storage/tmp",
    "./storage/logs",
    "./storage/run",
    "./storage/openclaw/state",
    "./.pytest_cache",
    "./.mypy_cache",
    "./.ruff_cache",
    "./**/__pycache__",
    "./**/*.pyc",
    "./**/*.pyo",
]

SECRET_RE = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization|pgpassword|private[_-]?key)\s*[:=]\s*([^\s\"']{6,})"
)
DSN_RE = re.compile(r"postgresql(?:\+\w+)?://[^@\s\"']+:[^@\s\"']+@", re.I)
HIGH_RISK_SECRET_RE = re.compile(
    r"(?i)("
    r"postgresql(?:\+\w+)?://[^@\s\"']+:[^@\s\"']+@|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16})\b|"
    r"\b(?:PGPASSWORD|OPENAI_API_KEY|HF_TOKEN|HUGGINGFACE_TOKEN|BRAVE_SEARCH_API_KEY|AUTHORIZATION)\s*=\s*(?!<|CHANGEME|REPLACE|your-|example|placeholder)[^\s\"']{8,}"
    r")"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def parse_pg_env() -> dict[str, str]:
    env_path = PROJECT / ".secrets" / "postgres.env"
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not m:
            continue
        value = m.group(2).strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[m.group(1)] = value
    required = ["POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB"]
    missing = [key for key in required if key not in values]
    if missing:
        raise RuntimeError(f"missing postgres env keys: {missing}")
    return values


def run(label: str, args: list[str], *, cwd: Path = PROJECT, env: dict[str, str] | None = None, timeout: int = 1800) -> dict[str, Any]:
    start = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(args, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout)
    end = dt.datetime.now(dt.timezone.utc)
    logs = OUT_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = DSN_RE.sub("postgresql://<REDACTED>@", proc.stdout)
    stderr = DSN_RE.sub("postgresql://<REDACTED>@", proc.stderr)
    stdout = SECRET_RE.sub(lambda m: m.group(1) + "=<REDACTED>", stdout)
    stderr = SECRET_RE.sub(lambda m: m.group(1) + "=<REDACTED>", stderr)
    out_path = logs / f"{label}.stdout.txt"
    err_path = logs / f"{label}.stderr.txt"
    out_path.write_text(stdout, encoding="utf-8")
    err_path.write_text(stderr, encoding="utf-8")
    rec = {
        "label": label,
        "argv": [Path(args[0]).name, *args[1:]],
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "started_at_utc": start.isoformat(),
        "finished_at_utc": end.isoformat(),
        "stdout_path": str(out_path.relative_to(OUT_ROOT)),
        "stderr_path": str(err_path.relative_to(OUT_ROOT)),
        "stdout_sha256": sha256_file(out_path),
        "stderr_sha256": sha256_file(err_path),
    }
    with (OUT_ROOT / "COMMAND_INDEX.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}")
    return rec


def pg_env(values: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": values["POSTGRES_HOST"],
            "PGPORT": values["POSTGRES_PORT"],
            "PGDATABASE": values["POSTGRES_DB"],
            "PGUSER": values["POSTGRES_ADMIN_USER"],
            "PGPASSWORD": values["POSTGRES_ADMIN_PASSWORD"],
        }
    )
    return env


def write_docs(manifest: dict[str, Any]) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    DATABASE.mkdir(parents=True, exist_ok=True)
    (DOCS / "README_PORTABLE_REPRODUCTION.md").write_text(
        textwrap.dedent(
            f"""
            # epidemic-openclaw 便携复现包（不含模型）

            本包用于在另一台设备上复现当前疫情自动获取系统的可信数据 Dashboard，重点保证
            Hantavirus / Legionella / Nipah 三个新增病种的可信数据三联图可查看。

            ## 包内容

            - `project_tree_no_models.tar.gz`：项目工作树，不含模型、密钥、物理数据库、缓存和历史运行证据。
            - `database/epidemic_openclaw.pgcustom`：PostgreSQL custom-format 逻辑备份，包含业务数据。
            - `database/database_globals_no_passwords.sql`：角色/全局对象定义，不含密码。
            - `scripts/restore_database.sh`：本地恢复数据库的参考脚本。
            - `scripts/start_dashboard_portable.sh`：本地启动 Dashboard 的参考脚本。
            - `scripts/create_dashboard_auth.py`：生成本地登录凭据 hash 文件；不会输出密码。

            ## 明确不包含

            - 大模型目录 `models/`
            - Hugging Face / pip / npm / OpenClaw 缓存
            - `.secrets`、真实 `.env`、密码、token、SSH key、浏览器 session
            - PostgreSQL 物理 data directory
            - 历史 reports/backups/artifacts/runtime 证据目录

            ## 推荐目录

            ```bash
            mkdir -p /root/autodl-tmp
            tar -xzf project_tree_no_models.tar.gz -C /root/autodl-tmp
            mv /root/autodl-tmp/epidemic-openclaw-portable /root/autodl-tmp/epidemic-openclaw
            cd /root/autodl-tmp/epidemic-openclaw
            ```

            如果你解压到其他路径，也可以，只要后续环境变量指向正确项目根目录。

            ## 基础环境

            - Linux x86_64，建议 Ubuntu 22.04/24.04
            - Python 3.10
            - PostgreSQL 18（用于完整匹配当前项目）
            - 可选：Node.js / OpenClaw / vLLM / Qwen3-8B-AWQ，用于后续检索/模型功能

            三个新增病种的可信数据三联图不需要运行模型预测；当前任务中：

            ```text
            prediction_scope=DEFERRED_BY_USER_OUT_OF_SCOPE
            training_scope=NOT_APPLICABLE
            forecast_scope=NOT_APPLICABLE
            model_inference_scope=NOT_APPLICABLE
            ```

            ## Python 环境

            优先使用项目自带声明：

            ```bash
            cd /root/autodl-tmp/epidemic-openclaw
            python3.10 -m venv .venv
            source .venv/bin/activate
            python -m pip install --upgrade pip
            python -m pip install -e .
            # 如缺依赖，再参考 pyproject.toml / environment.yml / requirements/
            ```

            ## 恢复数据库

            1. 创建 PostgreSQL 数据库和本地角色。
            2. 设置本地密码环境变量。
            3. 执行：

            ```bash
            cd <本包解压目录>
            export PGHOST=127.0.0.1
            export PGPORT=5432
            export PGDATABASE=epidemic_openclaw
            export PGUSER=postgres
            export PGPASSWORD='<你的本地 postgres 密码>'
            bash scripts/restore_database.sh
            ```

            恢复脚本不包含任何生产密码。若你的 PostgreSQL 使用 peer/trust 认证，可不设置 `PGPASSWORD`。

            ## 生成 Dashboard 登录文件

            ```bash
            cd /root/autodl-tmp/epidemic-openclaw
            mkdir -p runtime/secrets
            python <本包解压目录>/scripts/create_dashboard_auth.py runtime/secrets/login_hash.json
            ```

            ## 启动 Dashboard

            ```bash
            cd /root/autodl-tmp/epidemic-openclaw
            export DASHBOARD_DATABASE_URL='postgresql+psycopg://epidemic_dashboard_ro:<本地密码>@127.0.0.1:5432/epidemic_openclaw'
            export DASHBOARD_AUTH_FILE="$PWD/runtime/secrets/login_hash.json"
            export DASHBOARD_PORT=8020
            bash <本包解压目录>/scripts/start_dashboard_portable.sh
            ```

            打开：

            - `http://127.0.0.1:8020/login`
            - `http://127.0.0.1:8020/p1-12/trusted?disease=hantavirus&country=FIN`
            - `http://127.0.0.1:8020/p1-12/trusted?disease=legionella&country=AUT`
            - `http://127.0.0.1:8020/p1-12/trusted?disease=nipah&country=BGD`

            ## 三个新增病种可查看入口

            详见 `docs/THREE_NEW_DISEASES_VIEWABLE_COUNTRIES.txt`。

            ## 当前打包源

            - source project: `{manifest["project_path"]}`
            - git HEAD: `{manifest["git_head"]}`
            - package id: `{manifest["run_id"]}`
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (DOCS / "ENVIRONMENT_AND_MODEL_NOTES.md").write_text(
        textwrap.dedent(
            """
            # 环境与模型说明

            ## 三联图最小运行

            可信数据三联图只依赖 PostgreSQL + Python Dashboard，不依赖 Qwen/vLLM。

            ## 完整项目可选模型

            如果你要恢复后续检索 Agent 或模型功能，请另行放置固定版本模型：

            ```text
            models/llm/Qwen3-8B-AWQ/
            ```

            模型不在本包内，需要按项目原规定来源、revision 和 SHA-256 独立下载校验。

            ## 不要迁移的内容

            不要把旧服务器 `.secrets`、登录 hash、SSH known_hosts、浏览器 session、真实 token 或旧端口隧道迁到新机器。
            每台新设备都应重新生成本地密码和登录 hash。
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (DOCS / "THREE_NEW_DISEASES_VIEWABLE_COUNTRIES.txt").write_text(
        textwrap.dedent(
            """
            新增三病种 Dashboard 可查看清单
            访问入口：http://127.0.0.1:8020/p1-12/trusted
            填写规则：disease 填病种 slug，country 填 ISO3 国家码。

            1) Hantavirus / Hantavirus-related disease / Puumala virus proxy
               disease=hantavirus
               可查看国家：
               - FIN  Finland  时间=2018-01-01 至 2026-07-12

            2) Legionella / Legionellosis / Legionnaires' disease
               disease=legionella
               可查看国家：
               - AUT Austria
               - BEL Belgium
               - BGR Bulgaria
               - CYP Cyprus
               - CZE Czechia
               - DEU Germany
               - DNK Denmark
               - ESP Spain
               - EST Estonia
               - FIN Finland
               - FRA France
               - GRC Greece
               - HRV Croatia
               - HUN Hungary
               - IRL Ireland
               - ISL Iceland
               - ITA Italy
               - LIE Liechtenstein
               - LTU Lithuania
               - LUX Luxembourg
               - LVA Latvia
               - MLT Malta
               - NLD Netherlands
               - NOR Norway
               - POL Poland
               - PRT Portugal
               - ROU Romania
               - SVK Slovakia
               - SVN Slovenia
               - SWE Sweden

            3) Nipah / Nipah virus disease
               disease=nipah
               可查看国家：
               - BGD Bangladesh
               - IND India

            常用直达链接：
            - Hantavirus Finland: http://127.0.0.1:8020/p1-12/trusted?disease=hantavirus&country=FIN
            - Legionella Austria: http://127.0.0.1:8020/p1-12/trusted?disease=legionella&country=AUT
            - Legionella Germany: http://127.0.0.1:8020/p1-12/trusted?disease=legionella&country=DEU
            - Legionella France: http://127.0.0.1:8020/p1-12/trusted?disease=legionella&country=FRA
            - Nipah Bangladesh: http://127.0.0.1:8020/p1-12/trusted?disease=nipah&country=BGD
            - Nipah India: http://127.0.0.1:8020/p1-12/trusted?disease=nipah&country=IND
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (SCRIPTS / "create_dashboard_auth.py").write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import getpass
            import hashlib
            import json
            import secrets
            import sys
            from pathlib import Path

            out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("runtime/secrets/login_hash.json")
            username = input("Dashboard username [analyst]: ").strip() or "analyst"
            password = getpass.getpass("Dashboard password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise SystemExit("passwords do not match")
            salt = secrets.token_bytes(16)
            digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {"username": username, "salt_hex": salt.hex(), "hash_hex": digest.hex(), "n": 16384, "r": 8, "p": 1},
                    indent=2,
                    sort_keys=True,
                )
                + "\\n",
                encoding="utf-8",
            )
            out.chmod(0o600)
            print(f"wrote {out}")
            """
        ),
        encoding="utf-8",
    )
    (SCRIPTS / "start_dashboard_portable.sh").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            PROJECT_ROOT=${PROJECT_ROOT:-$(pwd -P)}
            PORT=${DASHBOARD_PORT:-8020}
            : "${DASHBOARD_DATABASE_URL:?set DASHBOARD_DATABASE_URL}"
            : "${DASHBOARD_AUTH_FILE:?set DASHBOARD_AUTH_FILE}"

            for key in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY GIT_CONFIG; do
              unset "$key" || true
            done

            export PYTHONPATH="$PROJECT_ROOT/src"
            export APP_ENV="${APP_ENV:-production}"
            export EPIDEMIC_INTERFACE_VERSION="${EPIDEMIC_INTERFACE_VERSION:-p1-10r3-v1}"
            export EPIDEMIC_DEPLOYMENT_COMMIT="${EPIDEMIC_DEPLOYMENT_COMMIT:-0000000000000000000000000000000000000000}"

            exec python -m uvicorn epidemic_core.dashboard.app:create_app \
              --factory --host 127.0.0.1 --port "$PORT" --no-access-log
            """
        ),
        encoding="utf-8",
    )
    (SCRIPTS / "restore_database.sh").write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            HERE=$(cd -- "$(dirname "$0")/.." && pwd -P)
            DB_DUMP="$HERE/database/epidemic_openclaw.pgcustom"
            GLOBALS="$HERE/database/database_globals_no_passwords.sql"

            : "${PGHOST:=127.0.0.1}"
            : "${PGPORT:=5432}"
            : "${PGDATABASE:=epidemic_openclaw}"
            : "${PGUSER:=postgres}"

            command -v psql >/dev/null
            command -v pg_restore >/dev/null

            psql -v ON_ERROR_STOP=1 -d postgres -f "$GLOBALS"
            psql -v ON_ERROR_STOP=1 -d postgres -c "SELECT 'CREATE DATABASE ' || quote_ident('${PGDATABASE}') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='${PGDATABASE}')" -tA | psql -v ON_ERROR_STOP=1 -d postgres
            pg_restore --clean --if-exists --no-owner --dbname "$PGDATABASE" "$DB_DUMP"

            cat <<'MSG'
            Database restore complete.

            If you use password authentication, set local passwords for these roles as needed:
              ALTER ROLE epidemic_dashboard_ro PASSWORD '<local password>';
              ALTER ROLE epidemic_app PASSWORD '<local password>';
              ALTER ROLE epidemicreview PASSWORD '<local password>';
            MSG
            """
        ),
        encoding="utf-8",
    )
    for script in SCRIPTS.glob("*.sh"):
        script.chmod(0o755)


def project_secret_scan() -> dict[str, Any]:
    hits = []
    scanned = 0
    skipped = 0
    excluded_parts = {".git", ".secrets", "models", "node_modules", ".venv", "venv", "__pycache__"}
    excluded_prefixes = (
        "storage/cache/",
        "storage/database",
        "storage/database_snapshots/",
        "storage/database_backups/",
        "storage/secrets/",
        "storage/tmp/",
        "storage/logs/",
        "storage/run/",
        "storage/openclaw/state/",
        "reports/",
        "backups/",
        "artifacts/",
        "runtime/",
    )
    forbidden_included_prefixes = (".secrets/", "private/", "storage/secrets/")
    forbidden_included_suffixes = (".pem", ".key", ".p12", ".pfx", "token.env")
    for path in PROJECT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PROJECT)
        rel_posix = rel.as_posix()
        parts = set(rel.parts)
        if parts & excluded_parts or rel_posix.startswith(excluded_prefixes):
            skipped += 1
            continue
        if rel_posix.startswith(forbidden_included_prefixes) or rel_posix.endswith(forbidden_included_suffixes):
            hits.append({"path": rel_posix, "reason": "forbidden secret-bearing path would be included"})
            continue
        try:
            if path.stat().st_size > 2_000_000:
                skipped += 1
                continue
            data = path.read_bytes()
            text = data.decode("utf-8")
        except Exception:
            skipped += 1
            continue
        scanned += 1
        if HIGH_RISK_SECRET_RE.search(text):
            hits.append({"path": rel_posix, "reason": "high-risk secret-like literal"})
    return {"files_scanned": scanned, "files_skipped": skipped, "hits": hits, "secret_hit_count": len(hits)}


def main() -> None:
    if OUT_ROOT.exists():
        raise SystemExit(f"output exists: {OUT_ROOT}")
    for path in (PAYLOAD, DOCS, SCRIPTS, DATABASE):
        path.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "COMMAND_INDEX.jsonl").touch()

    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(PROJECT), text=True).strip()
    dirty_count = int(subprocess.check_output("git status --porcelain=v1 | wc -l", cwd=str(PROJECT), shell=True, text=True).strip())
    manifest: dict[str, Any] = {
        "run_id": RUN_ID,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project_path": str(PROJECT),
        "git_head": git_head,
        "dirty_path_count": dirty_count,
        "excluded_patterns": EXCLUDES,
        "models_included": False,
        "secrets_included": False,
        "physical_postgres_data_included": False,
        "database_logical_dump_included": True,
    }
    write_docs(manifest)

    values = parse_pg_env()
    env = pg_env(values)
    run(
        "pg_dump_globals_no_passwords",
        [str(PG_BIN / "pg_dumpall"), "--globals-only", "--no-role-passwords", "--file", str(DATABASE / "database_globals_no_passwords.sql")],
        env=env,
        timeout=600,
    )
    run(
        "pg_dump_custom_full_database",
        [
            str(PG_BIN / "pg_dump"),
            "--format=custom",
            "--compress=6",
            "--no-owner",
            "--file",
            str(DATABASE / "epidemic_openclaw.pgcustom"),
            values["POSTGRES_DB"],
        ],
        env=env,
        timeout=3600,
    )
    run(
        "pg_restore_list_verify",
        [str(PG_BIN / "pg_restore"), "--list", str(DATABASE / "epidemic_openclaw.pgcustom")],
        cwd=PAYLOAD,
        timeout=600,
    )

    project_tree = PAYLOAD / "project_tree_no_models.tar.gz"
    tar_args = ["tar", "--warning=no-file-changed"]
    for pattern in EXCLUDES:
        tar_args.append(f"--exclude={pattern}")
    tar_args += ["-czf", str(project_tree), "-C", str(PROJECT), "--transform", "s,^\\.,epidemic-openclaw-portable,", "."]
    run("tar_project_tree_no_models", tar_args, timeout=3600)

    scan = project_secret_scan()
    write_json(PAYLOAD / "SECRET_SCAN_REPORT.json", scan)
    if scan["secret_hit_count"]:
        raise SystemExit("SECRET_SCAN_FAILED")

    files = []
    for path in sorted(PAYLOAD.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(PAYLOAD).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    manifest["payload_files"] = files
    manifest["state"] = "PASS"
    write_json(PAYLOAD / "PORTABLE_PACKAGE_MANIFEST.json", manifest)
    (PAYLOAD / "SHA256SUMS.txt").write_text(
        "".join(f"{item['sha256']}  {item['path']}\n" for item in files), encoding="utf-8"
    )

    final_archive = OUT_ROOT / f"{RUN_ID}.tar.gz"
    run("tar_final_portable_bundle", ["tar", "-czf", str(final_archive), "-C", str(PAYLOAD), "."], timeout=3600)
    final_sha = sha256_file(final_archive)
    (final_archive.with_suffix(".tar.gz.sha256")).write_text(f"{final_sha}  {final_archive.name}\n", encoding="utf-8")
    write_json(
        OUT_ROOT / "FINAL_PORTABLE_BUNDLE_RECEIPT.json",
        {
            "state": "PASS",
            "run_id": RUN_ID,
            "archive": str(final_archive),
            "archive_size": final_archive.stat().st_size,
            "archive_sha256": final_sha,
            "manifest": str(PAYLOAD / "PORTABLE_PACKAGE_MANIFEST.json"),
            "secret_scan": scan,
        },
    )
    print(json.dumps({"state": "PASS", "run_id": RUN_ID, "archive": str(final_archive), "sha256": final_sha}, ensure_ascii=False))


if __name__ == "__main__":
    main()
