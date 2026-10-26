#!/usr/bin/env bash
set -euo pipefail

REPORT_DIR="$1"
ISO_DB="$2"
PROJECT=USER_HOME/projects/openclaw
PG="$PROJECT/runtime/postgres18x/bin"
SOCKET="$PROJECT/runtime/postgres-run"
mkdir -p "$REPORT_DIR/backup"
BACKUP="$REPORT_DIR/backup/epidemic_openclaw_full_before_r2.dump"
LIST="$REPORT_DIR/backup/epidemic_openclaw_full_before_r2.pg_restore_list.txt"
PROOF="$REPORT_DIR/R2_BACKUP_RESTORE_PROOF.json"

cd "$PROJECT"

exists="$($PG/psql -h "$SOCKET" -p 55432 -U postgres -d postgres -X -tAc "select count(*) from pg_database where datname='$ISO_DB'")"
if [ "$exists" != "0" ]; then
  python3 - <<PY
import json
from datetime import datetime, timezone
open("$PROOF","w",encoding="utf-8").write(json.dumps({
  "schema_version":"1.0",
  "task_id":"KG_P1_R3R_UX2_R2_70PDF_BASE_BACKFILL",
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "active_project_root":"$PROJECT",
  "source_revision_or_hash":"UNKNOWN",
  "database_identity":{"database":"epidemic_openclaw","port":55432},
  "input_manifest_sha256":"",
  "result_status":"BLOCKED_ISOLATED_DB_ALREADY_EXISTS",
  "commands_or_evidence_refs":[],
  "blockers":["$ISO_DB already exists"],
  "no_fake_pass_assertion":True
},ensure_ascii=False,indent=2)+"\n")
PY
  exit 2
fi

"$PG/pg_dump" -h "$SOCKET" -p 55432 -U postgres -d epidemic_openclaw -Fc -f "$BACKUP"
"$PG/pg_restore" --list "$BACKUP" > "$LIST"
"$PG/createdb" -h "$SOCKET" -p 55432 -U postgres "$ISO_DB"
"$PG/pg_restore" -h "$SOCKET" -p 55432 -U postgres -d "$ISO_DB" "$BACKUP"

sha="$(sha256sum "$BACKUP" | awk '{print $1}')"
size="$(wc -c < "$BACKUP" | tr -d ' ')"
list_sha="$(sha256sum "$LIST" | awk '{print $1}')"
counts="$($PG/psql -h "$SOCKET" -p 55432 -U postgres -d "$ISO_DB" -X -tAc "select (select count(*) from epidemic_kg.kg_nodes),(select count(*) from epidemic_kg.kg_edges),(select count(*) from epidemic_kg.kg_claims),(select version_num from epidemic.alembic_version)")"
python3 - <<PY
import json
from datetime import datetime, timezone
open("$PROOF","w",encoding="utf-8").write(json.dumps({
  "schema_version":"1.0",
  "task_id":"KG_P1_R3R_UX2_R2_70PDF_BASE_BACKFILL",
  "generated_at_utc":datetime.now(timezone.utc).isoformat(),
  "active_project_root":"$PROJECT",
  "source_revision_or_hash":"UNKNOWN",
  "database_identity":{"database":"epidemic_openclaw","port":55432},
  "input_manifest_sha256":"",
  "result_status":"BACKUP_RESTORE_VERIFIED",
  "commands_or_evidence_refs":["pg_dump -Fc full database","pg_restore --list","createdb isolated","pg_restore isolated","isolated count query"],
  "blockers":[],
  "no_fake_pass_assertion":True,
  "backup_path":"$BACKUP",
  "backup_size_bytes":int("$size"),
  "backup_sha256":"$sha",
  "restore_list_path":"$LIST",
  "restore_list_sha256":"$list_sha",
  "isolated_database":"$ISO_DB",
  "isolated_counts_raw":"$counts"
},ensure_ascii=False,indent=2)+"\n")
PY
echo "$ISO_DB"
