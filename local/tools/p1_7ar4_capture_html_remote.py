from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil

from fastapi.testclient import TestClient


RUN_ID = "eb873976-d1cc-4e61-ac7f-5d404bbc2e1b"
RUNTIME = pathlib.Path("USER_HOME/autodl-tmp/epidemic-source-review-runtime")
OUTPUT = pathlib.Path("/tmp/p1_7ar4_browser_harness")


def load_service_environment() -> None:
    pid = (RUNTIME / "run/review.pid").read_text(encoding="ascii").strip()
    entries = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    for entry in entries:
        if not entry:
            continue
        key, _, value = entry.partition(b"=")
        name = key.decode("utf-8")
        if name in {"REVIEW_DATABASE_URL", "PYTHONPATH"}:
            os.environ[name] = value.decode("utf-8")


def auth_fixture(path: pathlib.Path, password: str) -> None:
    salt = os.urandom(16)
    values = {"username": 'REDACTED', "salt_hex": salt.hex(), "n": 16384, "r": 8, "p": 1}
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=values["n"], r=values["r"], p=values["p"], dklen=32
    )
    values["hash_hex"] = digest.hex()
    path.write_text(json.dumps(values), encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    load_service_environment()
    output = OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    auth = output / ".ephemeral-auth.json"
    password = os.urandom(32).hex()
    auth_fixture(auth, password)
    os.environ["REVIEW_AUTH_FILE"] = str(auth)
    from epidemic_core.discovery.review_app import create_app

    try:
        with TestClient(create_app()) as client:
            login = client.post(
                "/login",
                data={"username": 'REDACTED', "password": password},
                follow_redirects=False,
            )
            if login.status_code != 303:
                raise RuntimeError("ephemeral harness authentication failed")
            response = client.get(f"/discoveries/{RUN_ID}")
            if response.status_code != 200:
                raise RuntimeError(f"detail render failed: {response.status_code}")
            html = response.text
            required = (
                "BLOCKED_GENERIC_SEARCH_CONFIGURATION",
                "DataCite Public DOI Metadata API",
                "api.datacite.org",
            )
            if any(value not in html for value in required):
                raise RuntimeError("required terminal evidence is absent")
            (output / "index.html").write_text(html, encoding="utf-8")
            no_js = html.replace(
                '<script src="/static/review.p1-7ar3-v1.js" defer></script>', ""
            )
            (output / "index-no-js.html").write_text(no_js, encoding="utf-8")
            static = output / "static"
            static.mkdir(exist_ok=True)
            for asset in (RUNTIME / "src/epidemic_core/discovery/static").glob("review.*"):
                shutil.copy2(asset, static / asset.name)
    finally:
        auth.unlink(missing_ok=True)
    print(json.dumps({"status": "PASS", "run_id": RUN_ID, "html_sha256": hashlib.sha256(html.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
