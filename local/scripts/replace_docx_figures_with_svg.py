"""Replace four embedded DOCX figure rasters with supplied SVG originals.

This script is deliberately package-level: it preserves all Word XML, text,
styles, layout, captions, and every non-target media asset byte-for-byte.
Only the four figure media entries, their document relationships, and the
package SVG content-type declaration are changed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


PKG_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("", PKG_NS)
ET.register_namespace("", REL_NS)

TARGETS = {
    "image15.png": "fig1.svg",
    "image16.png": "fig2.svg",
    "image17.png": "fig3.svg",
    "image18.png": "fig4.svg",
}
CONTENT_TYPES_PART = "[Content_Types].xml"
RELS_PART = "word/_rels/document.xml.rels"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_docx", type=Path)
    parser.add_argument("svg_directory", type=Path)
    parser.add_argument("output_docx", type=Path)
    return parser.parse_args()


def update_relationships(data: bytes) -> bytes:
    root = ET.fromstring(data)
    expected_targets = {f"media/{name}": f"media/{Path(svg).stem}.svg" for name, svg in TARGETS.items()}
    found: set[str] = set()
    for relationship in root.findall(f"{{{REL_NS}}}Relationship"):
        target = relationship.get("Target")
        if target in expected_targets:
            relationship.set("Target", expected_targets[target])
            found.add(target)
    if found != set(expected_targets):
        missing = sorted(set(expected_targets) - found)
        raise RuntimeError(f"Missing expected image relationships: {missing}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def update_content_types(data: bytes) -> bytes:
    root = ET.fromstring(data)
    defaults = root.findall(f"{{{PKG_NS}}}Default")
    svg_defaults = [entry for entry in defaults if entry.get("Extension", "").lower() == "svg"]
    if svg_defaults:
        for entry in svg_defaults:
            entry.set("ContentType", "image/svg+xml")
    else:
        ET.SubElement(root, f"{{{PKG_NS}}}Default", {
            "Extension": "svg",
            "ContentType": "image/svg+xml",
        })
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_entry(out_zip: zipfile.ZipFile, info: zipfile.ZipInfo, name: str, data: bytes) -> None:
    copied = copy.copy(info)
    copied.filename = name
    out_zip.writestr(copied, data, compress_type=info.compress_type)


def main() -> int:
    args = parse_args()
    if not args.source_docx.is_file():
        raise FileNotFoundError(args.source_docx)
    if not args.svg_directory.is_dir():
        raise NotADirectoryError(args.svg_directory)
    if args.output_docx.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {args.output_docx}")

    svg_bytes: dict[str, bytes] = {}
    for source_name in TARGETS.values():
        path = args.svg_directory / source_name
        data = path.read_bytes()
        ET.fromstring(data)  # Confirm that the supplied asset is parseable SVG/XML.
        svg_bytes[source_name] = data

    target_media_parts = {f"word/media/{name}": svg for name, svg in TARGETS.items()}

    with zipfile.ZipFile(args.source_docx, "r") as source_zip:
        source_infos = source_zip.infolist()
        source_names = [info.filename for info in source_infos]
        required = {CONTENT_TYPES_PART, RELS_PART, *target_media_parts}
        missing = required - set(source_names)
        if missing:
            raise RuntimeError(f"Source DOCX lacks required parts: {sorted(missing)}")

        source_data = {info.filename: source_zip.read(info.filename) for info in source_infos}
        updated_rels = update_relationships(source_data[RELS_PART])
        updated_content_types = update_content_types(source_data[CONTENT_TYPES_PART])

        args.output_docx.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output_docx, "w") as output_zip:
            for info in source_infos:
                name = info.filename
                if name == CONTENT_TYPES_PART:
                    write_entry(output_zip, info, name, updated_content_types)
                elif name == RELS_PART:
                    write_entry(output_zip, info, name, updated_rels)
                elif name in target_media_parts:
                    svg_name = target_media_parts[name]
                    write_entry(output_zip, info, f"word/media/{svg_name}", svg_bytes[svg_name])
                else:
                    write_entry(output_zip, info, name, source_data[name])

    with zipfile.ZipFile(args.output_docx, "r") as output_zip:
        if output_zip.testzip() is not None:
            raise RuntimeError("DOCX ZIP integrity test failed")
        output_names = set(output_zip.namelist())
        expected_names = (set(source_names) - set(target_media_parts)) | {
            f"word/media/{svg_name}" for svg_name in TARGETS.values()
        }
        if output_names != expected_names:
            raise RuntimeError("Output package entry list differs from the expected targeted replacement")

        for name in source_names:
            if name in {CONTENT_TYPES_PART, RELS_PART, *target_media_parts}:
                continue
            if digest(output_zip.read(name)) != digest(source_data[name]):
                raise RuntimeError(f"Unexpected content change in {name}")

        rels_root = ET.fromstring(output_zip.read(RELS_PART))
        rel_targets = {item.get("Target") for item in rels_root.findall(f"{{{REL_NS}}}Relationship")}
        for svg_name in TARGETS.values():
            if f"media/{svg_name}" not in rel_targets:
                raise RuntimeError(f"Relationship not updated for {svg_name}")
            media_part = f"word/media/{svg_name}"
            if output_zip.read(media_part) != svg_bytes[svg_name]:
                raise RuntimeError(f"SVG payload mismatch for {svg_name}")

        content_types_root = ET.fromstring(output_zip.read(CONTENT_TYPES_PART))
        svg_content_type = any(
            item.get("Extension", "").lower() == "svg" and item.get("ContentType") == "image/svg+xml"
            for item in content_types_root.findall(f"{{{PKG_NS}}}Default")
        )
        if not svg_content_type:
            raise RuntimeError("SVG content type is missing")

    print(f"output={args.output_docx}")
    print("replaced=image15.png->fig1.svg,image16.png->fig2.svg,image17.png->fig3.svg,image18.png->fig4.svg")
    print("unchanged_parts_verified=all_non_target_parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
