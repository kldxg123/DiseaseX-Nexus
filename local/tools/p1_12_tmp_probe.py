from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_12 as p


def main() -> None:
    client, _gate = p.p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p.p111.p110r3.previous.p19.a100.paths(client)
        py = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r"""
import json
import os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url

url = make_url(os.environ["MIGRATION_DATABASE_URL"]).set(
    username="epidemicdb",
    password=os.environ["POSTGRES_ADMIN_PASSWORD"],
)
engine = sa.create_engine(url, hide_parameters=True)
objects = [
    "epidemic_preview.active_preview_publications_v1",
    "epidemic_preview.preview_dashboard_series_v1",
    "epidemic_preview.preview_lineage_v1",
    "epidemic_preview.preview_review_cards_v1",
]
with engine.connect() as db:
    print(json.dumps({
        "revision": db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),
        "objects": {
            name: db.execute(text(f"SELECT to_regclass('{name}')::text")).scalar_one()
            for name in objects
        },
    }, sort_keys=True))
"""
        setup = (
            f"source {p.q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {p.q(project + '/.secrets/postgres.env')}; set +a; "
            f"{p.q(py)} -c {p.q(code)}"
        )
        print(p.p111.p110r3.previous.p19.a100.checked(client, f"bash -lc {p.q(setup)}", timeout=120))
    finally:
        client.close()


if __name__ == "__main__":
    main()
