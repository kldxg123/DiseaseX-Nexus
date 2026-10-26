from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, write_json


GITHUB_API = "https://api.github.com"
RAW_GITHUB = "https://raw.githubusercontent.com"
KNOWN_DATA_FILES = [
    "ebola_data_db_format.csv",
    "ebola_africa.csv",
    "weekly_cases.json",
    "cmrivers_chunks.json",
]


def _request_json(url: str, timeout: int = 30) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ebola-step-p0-data-loader"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_bytes(url: str, timeout: int = 60) -> bytes:
    headers = {"User-Agent": "ebola-step-p0-data-loader"}
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def list_github_files(repo: str, subdir: str = "data", ref: str | None = None, timeout: int = 30) -> list[dict]:
    suffix = f"?ref={ref}" if ref else ""
    url = f"{GITHUB_API}/repos/{repo}/contents/{subdir}{suffix}"
    payload = _request_json(url, timeout=timeout)
    if isinstance(payload, dict):
        payload = [payload]
    files: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "dir":
            files.extend(list_github_files(repo, item["path"], ref=ref, timeout=timeout))
        elif item.get("type") == "file":
            files.append(item)
    return files


def download_github_data(repo: str, out_dir: str | Path, subdir: str = "data", ref: str | None = None) -> dict:
    out = ensure_dir(out_dir)
    manifest = {
        "repo": repo,
        "subdir": subdir,
        "downloaded_files": [],
        "csv_files": [],
        "status": "started",
        "error_type": None,
        "error": None,
    }
    try:
        files = list_github_files(repo, subdir=subdir, ref=ref)
        for item in files:
            name = item.get("name", "")
            download_url = item.get("download_url")
            if not download_url:
                continue
            rel = Path(item.get("path", name))
            target = out / rel.name
            if target.suffix.lower() not in {".csv", ".json", ".txt"}:
                continue
            target.write_bytes(_download_bytes(download_url))
            manifest["downloaded_files"].append(str(target))
            if target.suffix.lower() == ".csv":
                manifest["csv_files"].append(str(target))
        manifest["status"] = "ok"
    except Exception as exc:
        fallback = _download_known_raw_files(repo, out, subdir=subdir, ref=ref or "main")
        if fallback["csv_files"]:
            manifest["downloaded_files"] = fallback["downloaded_files"]
            manifest["csv_files"] = fallback["csv_files"]
            manifest["status"] = "ok_raw_fallback"
            manifest["error_type"] = type(exc).__name__
            manifest["error"] = str(exc)[:500]
            write_json(out / "github_data_manifest.json", manifest)
            return manifest
        cached_csvs = sorted(str(path) for path in out.glob("*.csv"))
        cached_files = sorted(str(path) for path in out.glob("*") if path.is_file() and path.name != "github_data_manifest.json")
        manifest["downloaded_files"] = cached_files
        manifest["csv_files"] = cached_csvs
        manifest["status"] = "using_cached" if cached_csvs else "blocked"
        manifest["error_type"] = type(exc).__name__
        manifest["error"] = str(exc)[:500]
    write_json(out / "github_data_manifest.json", manifest)
    return manifest


def _download_known_raw_files(repo: str, out: Path, subdir: str = "data", ref: str = "main") -> dict:
    downloaded_files: list[str] = []
    csv_files: list[str] = []
    for name in KNOWN_DATA_FILES:
        url = f"{RAW_GITHUB}/{repo}/{ref}/{subdir}/{name}"
        target = out / name
        try:
            target.write_bytes(_download_bytes(url))
        except Exception:
            continue
        downloaded_files.append(str(target))
        if target.suffix.lower() == ".csv":
            csv_files.append(str(target))
    return {"downloaded_files": downloaded_files, "csv_files": csv_files}


def main() -> int:
    parser = argparse.ArgumentParser(description="Download configured Ebola case data from GitHub.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--repo", default="")
    parser.add_argument("--subdir", default="data")
    parser.add_argument("--ref", default="")
    args = parser.parse_args()
    cfg = load_config(args.config)
    repo = args.repo or cfg["sources"]["cases"].get("github_repo", "houzengxiang-14588/ebola-prediction")
    out_dir = resolve_path(cfg["paths"]["data_raw"]) / "github" / repo.replace("/", "__")
    manifest = download_github_data(repo, out_dir=out_dir, subdir=args.subdir, ref=args.ref or None)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "ok" and manifest["csv_files"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
