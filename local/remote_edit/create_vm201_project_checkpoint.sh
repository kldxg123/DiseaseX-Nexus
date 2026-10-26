#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=USER_HOME/projects/openclaw
PG_BIN="$ROOT/runtime/postgres18x/bin"
PG_SOCKET="$ROOT/runtime/postgres-run"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CHECKPOINT_ID="epidemic_openclaw_checkpoint_${STAMP}"
OUT="$ROOT/runtime/checkpoints/$CHECKPOINT_ID"
REPORT="$ROOT/reports/$CHECKPOINT_ID"

mkdir -p "$OUT" "$REPORT"
chmod 700 "$OUT"

cd "$ROOT"
HEAD_BEFORE="$(git rev-parse HEAD)"
BRANCH="$(git branch --show-current)"
STATUS_BEFORE_SHA="$(git status --porcelain=v1 -uall | sha256sum | awk '{print $1}')"
STATUS_BEFORE_COUNT="$(git status --porcelain=v1 -uall | wc -l)"

SAFE_PATHS=(
  AGENTS.md
  THIRD_PARTY_LICENSES.md
  config
  environment.yml
  integrations
  migrations
  requirements
  src
  tests
  tools
)

CANDIDATES="$OUT/candidate_files.zlist"
git ls-files -z --cached --others --exclude-standard -- "${SAFE_PATHS[@]}" > "$CANDIDATES"

candidate_count=0
candidate_bytes=0
suspicious_name_count=0
large_candidate_count=0
content_secret_match_count=0

while IFS= read -r -d '' path; do
  [[ -f "$path" ]] || continue
  candidate_count=$((candidate_count + 1))
  size="$(stat -c %s -- "$path")"
  candidate_bytes=$((candidate_bytes + size))
  base="$(basename -- "$path")"
  case "$base" in
    .env|.env.*|*.pem|*.key|*.p12|*.pfx|.pgpass|known_hosts*|*id_rsa*|*id_ed25519*)
      [[ "$base" == ".env.example" ]] || suspicious_name_count=$((suspicious_name_count + 1))
      ;;
  esac
  if (( size > 52428800 )); then
    large_candidate_count=$((large_candidate_count + 1))
  fi
  if (( size <= 5242880 )) && grep -IlEq --binary-files=without-match -- \
      '-----BEGIN (OPENSSH|RSA|EC|DSA) PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{24,}|sk-[A-Za-z0-9_-]{32,}' \
      "$path"; then
    content_secret_match_count=$((content_secret_match_count + 1))
  fi
done < "$CANDIDATES"

if (( suspicious_name_count != 0 || large_candidate_count != 0 || content_secret_match_count != 0 )); then
  printf 'checkpoint_preflight=FAIL\ncandidate_count=%s\ncandidate_bytes=%s\nsuspicious_name_count=%s\nlarge_candidate_count=%s\ncontent_secret_match_count=%s\n' \
    "$candidate_count" "$candidate_bytes" "$suspicious_name_count" "$large_candidate_count" "$content_secret_match_count" \
    > "$REPORT/PREFLIGHT_FAILURE.txt"
  exit 42
fi

git add -A -- "${SAFE_PATHS[@]}"
if git diff --cached --check > "$OUT/git_diff_cached_check.txt" 2>&1; then
  whitespace_check=PASS
else
  whitespace_check=WARNING_EXISTING_CONTENT
fi

if ! git diff --cached --quiet; then
  git commit -m "chore(checkpoint): save recoverable project state ${STAMP}"
fi

HEAD_AFTER="$(git rev-parse HEAD)"
TAG="checkpoint-${STAMP}"
git tag -a "$TAG" "$HEAD_AFTER" -m "Recoverable project checkpoint ${STAMP}"

BUNDLE="$OUT/${CHECKPOINT_ID}.bundle"
git bundle create "$BUNDLE" --all
git bundle verify "$BUNDLE" > "$OUT/git_bundle_verify.txt" 2>&1

DB_DUMP="$OUT/epidemic_openclaw_${STAMP}.pgcustom"
PGHOST="$PG_SOCKET" PGPORT=55432 PGUSER=postgres \
  "$PG_BIN/pg_dump" --format=custom --no-password --file="$DB_DUMP" epidemic_openclaw
sha256sum "$DB_DUMP" > "$DB_DUMP.sha256"
"$PG_BIN/pg_restore" --list "$DB_DUMP" > "$DB_DUMP.list"
PG_RESTORE_ENTRIES="$(wc -l < "$DB_DUMP.list")"

git status --porcelain=v1 -uall > "$OUT/git_status_after.txt"
STATUS_AFTER_SHA="$(sha256sum "$OUT/git_status_after.txt" | awk '{print $1}')"
STATUS_AFTER_COUNT="$(wc -l < "$OUT/git_status_after.txt")"

find runtime/user_uploads runtime/kg_imports storage experiments reports \
  -type f -printf '%s\t%T@\t%p\n' 2>/dev/null \
  | LC_ALL=C sort -k3,3 > "$OUT/external_asset_inventory.tsv"
sha256sum "$OUT/external_asset_inventory.tsv" > "$OUT/external_asset_inventory.tsv.sha256"

cat > "$OUT/RESTORE_CHECKPOINT.sh" <<'RESTORE'
#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: $0 --code-target ABSOLUTE_EMPTY_DIR [--restore-db CONFIRM_REPLACE_DATABASE]"
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$(find "$SCRIPT_DIR" -maxdepth 1 -name '*.bundle' -print -quit)"
DUMP="$(find "$SCRIPT_DIR" -maxdepth 1 -name '*.pgcustom' -print -quit)"
PG_BIN=USER_HOME/projects/openclaw/runtime/postgres18x/bin
PG_SOCKET=USER_HOME/projects/openclaw/runtime/postgres-run
CODE_TARGET=""
RESTORE_DB=""

while (( $# )); do
  case "$1" in
    --code-target) CODE_TARGET="$2"; shift 2 ;;
    --restore-db) RESTORE_DB="$2"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n "$CODE_TARGET" && "$CODE_TARGET" = /* ]] || { usage; exit 2; }
[[ ! -e "$CODE_TARGET" ]] || { echo "Target already exists; refusing to overwrite."; exit 3; }

git clone "$BUNDLE" "$CODE_TARGET"
git -C "$CODE_TARGET" checkout "$(git -C "$CODE_TARGET" tag --list 'checkpoint-*' --sort=-creatordate | head -1)"

if [[ "$RESTORE_DB" == "CONFIRM_REPLACE_DATABASE" ]]; then
  echo "Database restore was explicitly requested. Ensure application services are stopped before proceeding."
  PGHOST="$PG_SOCKET" PGPORT=55432 PGUSER=postgres "$PG_BIN/pg_restore" \
    --clean --if-exists --no-owner --no-privileges --dbname=epidemic_openclaw "$DUMP"
else
  echo "Code restored. Database was not changed. Use --restore-db CONFIRM_REPLACE_DATABASE only during an approved maintenance window."
fi
RESTORE
chmod 700 "$OUT/RESTORE_CHECKPOINT.sh"

BUNDLE_SHA="$(sha256sum "$BUNDLE" | awk '{print $1}')"
DB_SHA="$(awk '{print $1}' "$DB_DUMP.sha256")"

cat > "$OUT/CHECKPOINT_MANIFEST.txt" <<EOF
checkpoint_id=$CHECKPOINT_ID
created_utc=$STAMP
project_root=$ROOT
git_branch=$BRANCH
git_head_before=$HEAD_BEFORE
git_head_after=$HEAD_AFTER
git_tag=$TAG
git_status_before_count=$STATUS_BEFORE_COUNT
git_status_before_sha256=$STATUS_BEFORE_SHA
git_status_after_count=$STATUS_AFTER_COUNT
git_status_after_sha256=$STATUS_AFTER_SHA
candidate_source_file_count=$candidate_count
candidate_source_bytes=$candidate_bytes
secret_filename_matches=0
high_confidence_secret_content_matches=0
large_candidate_files=0
git_diff_cached_whitespace_check=$whitespace_check
git_bundle=$(basename "$BUNDLE")
git_bundle_sha256=$BUNDLE_SHA
database_dump=$(basename "$DB_DUMP")
database_dump_sha256=$DB_SHA
pg_restore_list_entries=$PG_RESTORE_ENTRIES
external_asset_inventory=external_asset_inventory.tsv
external_asset_inventory_is_reference_only=true
external_large_assets_copied=false
services_restarted=false
database_mutation=pg_dump_read_only_only
push_performed=false
EOF

sha256sum "$OUT/CHECKPOINT_MANIFEST.txt" "$OUT/RESTORE_CHECKPOINT.sh" \
  "$OUT/git_bundle_verify.txt" "$OUT/git_status_after.txt" \
  "$OUT/external_asset_inventory.tsv" > "$OUT/SHA256SUMS"

cp "$OUT/CHECKPOINT_MANIFEST.txt" "$REPORT/"
cp "$OUT/SHA256SUMS" "$REPORT/"
cp "$OUT/git_bundle_verify.txt" "$REPORT/"

ARCHIVE="$ROOT/reports/${CHECKPOINT_ID}_portable.zip"
python3 - "$OUT" "$ARCHIVE" <<'PY'
import pathlib, sys, zipfile
source = pathlib.Path(sys.argv[1])
archive = pathlib.Path(sys.argv[2])
members = [p for p in source.iterdir() if p.is_file() and p.name != 'candidate_files.zlist']
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for path in sorted(members):
        zf.write(path, arcname=path.name)
with zipfile.ZipFile(archive) as zf:
    bad = zf.testzip()
    if bad is not None:
        raise SystemExit(f'ZIP verification failed at {bad}')
PY
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

printf 'checkpoint_status=COMPLETE\ncheckpoint_id=%s\ngit_head=%s\ngit_tag=%s\ncheckpoint_dir=%s\nportable_zip=%s\nportable_zip_sha256=%s\ndatabase_dump_sha256=%s\ngit_bundle_sha256=%s\nstatus_after_count=%s\n' \
  "$CHECKPOINT_ID" "$HEAD_AFTER" "$TAG" "$OUT" "$ARCHIVE" \
  "$(awk '{print $1}' "$ARCHIVE.sha256")" "$DB_SHA" "$BUNDLE_SHA" "$STATUS_AFTER_COUNT"
