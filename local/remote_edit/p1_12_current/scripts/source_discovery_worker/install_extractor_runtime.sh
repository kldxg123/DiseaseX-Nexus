#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
RUNTIME="$PROJECT/../epidemic-extractor-runtime"

install -d -m 0755 "$RUNTIME"
install -m 0555 "$PROJECT/src/epidemic_core/discovery/extractor.py" "$RUNTIME/extractor.py"
test -x /usr/bin/python3
setpriv --no-new-privs /usr/bin/python3 -B "$RUNTIME/extractor.py" --help >/dev/null
