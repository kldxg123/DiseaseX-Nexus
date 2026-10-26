from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import sys


def main() -> int:
    root = pathlib.Path("USER_HOME/projects/openclaw")
    auth_path = root / "runtime/secrets/dashboard_login_hash.json"
    password = sys.stdin.read().rstrip("\r\n")
    if not password:
        print("reset=FAILED_EMPTY_PASSWORD")
        return 2
    if not auth_path.exists():
        print("reset=FAILED_AUTH_FILE_MISSING")
        return 3

    current = json.loads(auth_path.read_text(encoding="utf-8"))
    username = current.get("username", "analyst")
    n = int(current.get("n", 16384))
    r = int(current.get("r", 8))
    p_factor = int(current.get("p", 1))

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = auth_path.with_name(f"{auth_path.name}.pre_reset_{timestamp}.bak")
    if backup_path.exists():
        print("reset=FAILED_BACKUP_EXISTS")
        return 4

    shutil.copy2(auth_path, backup_path)
    os.chmod(backup_path, 0o600)

    salt = secrets.token_bytes(16)
    hash_hex = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p_factor,
        dklen=32,
    ).hex()

    updated = dict(current)
    updated.update(
        {
            "username": username,
            "n": n,
            "r": r,
            "p": p_factor,
            "salt_hex": salt.hex(),
            "hash_hex": hash_hex,
        }
    )

    tmp_path = auth_path.with_name(f"{auth_path.name}.tmp_{timestamp}")
    tmp_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, auth_path)
    os.chmod(auth_path, 0o600)

    verify_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(updated["salt_hex"]),
        n=n,
        r=r,
        p=p_factor,
        dklen=32,
    ).hex()
    if verify_hash != updated["hash_hex"]:
        print("reset=FAILED_VERIFY")
        return 5

    print("reset=PASS")
    print(f"username={username}")
    print("backup_created=PASS_REDACTED")
    print(f"auth_file_mode={oct(auth_path.stat().st_mode & 0o777)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
