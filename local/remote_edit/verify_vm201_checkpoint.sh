#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=USER_HOME/projects/openclaw
ID=epidemic_openclaw_checkpoint_20260811T013554Z
OUT="$ROOT/runtime/checkpoints/$ID"
ZIP="$ROOT/reports/${ID}_portable.zip"

cd "$ROOT"
printf 'HEAD=%s\n' "$(git rev-parse HEAD)"
printf 'TAG_TARGET=%s\n' "$(git rev-list -n1 checkpoint-20260811T013554Z)"
git bundle verify "$OUT/$ID.bundle" >/dev/null
echo BUNDLE_VERIFY=PASS
"$ROOT/runtime/postgres18x/bin/pg_restore" --list "$OUT/epidemic_openclaw_20260811T013554Z.pgcustom" >/dev/null
echo PG_RESTORE_LIST=PASS
python3 - "$ZIP" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    print("ZIP_TEST=PASS" if zf.testzip() is None else "ZIP_TEST=FAIL")
    print(f"ZIP_MEMBERS={len(zf.namelist())}")
PY
(cd "$ROOT/reports" && sha256sum -c "${ID}_portable.zip.sha256")
stat -c 'ZIP_BYTES=%s' "$ZIP"
printf 'HEALTHZ='
wget -q -O- http://127.0.0.1:8020/healthz
echo
printf 'READYZ_HTTP='
wget -q -S -O /dev/null http://127.0.0.1:8020/readyz 2>&1 | awk '/HTTP\// {print $2; exit}'
