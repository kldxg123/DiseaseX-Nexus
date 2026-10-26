from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_requires_for_build_wheel(config_settings=None):  # noqa: ARG001
    return []


def get_requires_for_build_editable(config_settings=None):  # noqa: ARG001
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):  # noqa: ARG001
    return _write_dist_info(Path(metadata_directory))


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):  # noqa: ARG001
    return _write_dist_info(Path(metadata_directory))


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):  # noqa: ARG001
    return _build_project_wheel(Path(wheel_directory), editable=False)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):  # noqa: ARG001
    return _build_project_wheel(Path(wheel_directory), editable=True)


def _build_project_wheel(wheel_directory: Path, *, editable: bool) -> str:
    metadata = _project_metadata()
    dist_info_name = _dist_info_name(metadata["name"], metadata["version"])
    wheel_name = _wheel_name(metadata["name"], metadata["version"])
    wheel_path = wheel_directory / wheel_name
    wheel_directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        dist_info = temp_dir / dist_info_name
        _write_dist_info(temp_dir)
        records: list[tuple[Path, str, int]] = []
        if editable:
            pth_name = f"{_normalized_name(metadata['name'])}_editable.pth"
            pth_path = temp_dir / pth_name
            pth_path.write_text(
                "\n".join([str(PROJECT_ROOT), str(PROJECT_ROOT / "packages")]) + "\n",
                encoding="utf-8",
            )
            records.append(_record_for_file(pth_path, pth_name))
        else:
            for root in (PROJECT_ROOT / "packages", PROJECT_ROOT / "apps", PROJECT_ROOT / "scripts"):
                if root.exists():
                    _copy_tree(root, temp_dir / root.name, records)
        for path in sorted(dist_info.rglob("*")):
            if path.is_file() and path.name != "RECORD":
                records.append(_record_for_file(path, path.relative_to(temp_dir).as_posix()))
        record_path = dist_info / "RECORD"
        _write_record(record_path, records, dist_info_name)
        with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
            for path in sorted(temp_dir.rglob("*")):
                if path.is_file():
                    wheel.write(path, path.relative_to(temp_dir).as_posix())
    return wheel_name


def _write_dist_info(parent: Path) -> str:
    metadata = _project_metadata()
    dist_info_name = _dist_info_name(metadata["name"], metadata["version"])
    dist_info = parent / dist_info_name
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(_metadata_text(metadata), encoding="utf-8")
    (dist_info / "WHEEL").write_text(_wheel_text(), encoding="utf-8")
    entry_points = _entry_points_text(metadata["scripts"])
    if entry_points:
        (dist_info / "entry_points.txt").write_text(entry_points, encoding="utf-8")
    return dist_info_name


def _project_metadata() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project", {})
    return {
        "name": str(project.get("name") or "local-project"),
        "version": str(project.get("version") or "0.0.0"),
        "description": str(project.get("description") or ""),
        "requires_python": str(project.get("requires-python") or ""),
        "dependencies": list(project.get("dependencies") or []),
        "scripts": dict(project.get("scripts") or {}),
    }


def _metadata_text(metadata: dict) -> str:
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {metadata['name']}",
        f"Version: {metadata['version']}",
    ]
    if metadata["description"]:
        lines.append(f"Summary: {_one_line(metadata['description'])}")
    if metadata["requires_python"]:
        lines.append(f"Requires-Python: {metadata['requires_python']}")
    for dependency in metadata["dependencies"]:
        lines.append(f"Requires-Dist: {dependency}")
    lines.append("")
    return "\n".join(lines)


def _wheel_text() -> str:
    return "\n".join(
        [
            "Wheel-Version: 1.0",
            "Generator: local-editable-backend",
            "Root-Is-Purelib: true",
            "Tag: py3-none-any",
            "",
        ]
    )


def _entry_points_text(scripts: dict[str, str]) -> str:
    if not scripts:
        return ""
    lines = ["[console_scripts]"]
    for name, target in sorted(scripts.items()):
        lines.append(f"{name} = {target}")
    lines.append("")
    return "\n".join(lines)


def _write_record(record_path: Path, records: list[tuple[Path, str, int]], dist_info_name: str) -> None:
    with record_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for _path, relative, size in records:
            digest = _hash_file(_path)
            writer.writerow([relative, f"sha256={digest}", str(size)])
        writer.writerow([f"{dist_info_name}/RECORD", "", ""])


def _record_for_file(path: Path, relative: str) -> tuple[Path, str, int]:
    return path, relative.replace(os.sep, "/"), path.stat().st_size


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _copy_tree(source: Path, target: Path, records: list[tuple[Path, str, int]]) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source)
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        records.append(_record_for_file(out, out.relative_to(target.parent).as_posix()))


def _dist_info_name(name: str, version: str) -> str:
    return f"{_normalized_name(name)}-{_normalized_version(version)}.dist-info"


def _wheel_name(name: str, version: str) -> str:
    return f"{_normalized_name(name)}-{_normalized_version(version)}-py3-none-any.whl"


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _normalized_version(version: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.!+]+", "_", version)


def _one_line(text: str) -> str:
    return " ".join(text.split())
