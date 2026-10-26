"""Embed supplied SVGs as primary data for four existing DOCX figures.

The original PNGs remain byte-identical compatibility fallbacks. Word uses the
Office SVG extension as the primary image; renderers without SVG support use
the PNG fallback. All text, layout, image dimensions, captions, styles and
non-target package parts are preserved.
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
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
ASVG_NS = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
SVG_BLIP_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37B6A7EECFEC}"

ET.register_namespace("", PKG_NS)
ET.register_namespace("a", DRAWING_NS)
ET.register_namespace("r", DOC_REL_NS)
ET.register_namespace("asvg", ASVG_NS)

TARGETS = {
    "image15.png": "fig1.svg",
    "image16.png": "fig2.svg",
    "image17.png": "fig3.svg",
    "image18.png": "fig4.svg",
}
CONTENT_TYPES_PART = "[Content_Types].xml"
RELS_PART = "word/_rels/document.xml.rels"
DOCUMENT_PART = "word/document.xml"
IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_docx", type=Path)
    parser.add_argument("svg_directory", type=Path)
    parser.add_argument("output_docx", type=Path)
    return parser.parse_args()


def update_content_types(data: bytes) -> bytes:
    root = ET.fromstring(data)
    defaults = root.findall(f"{{{PKG_NS}}}Default")
    svg_defaults = [entry for entry in defaults if entry.get("Extension", "").lower() == "svg"]
    if svg_defaults:
        for entry in svg_defaults:
            entry.set("ContentType", "image/svg+xml")
    else:
        ET.SubElement(root, f"{{{PKG_NS}}}Default", {"Extension": "svg", "ContentType": "image/svg+xml"})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def update_relationships(data: bytes) -> tuple[bytes, dict[str, str]]:
    root = ET.fromstring(data)
    target_to_rel_id: dict[str, str] = {}
    existing_ids = {entry.get("Id") for entry in root.findall(f"{{{REL_NS}}}Relationship")}
    for relationship in root.findall(f"{{{REL_NS}}}Relationship"):
        target = relationship.get("Target")
        if target in {f"media/{name}" for name in TARGETS}:
            if relationship.get("Type") != IMAGE_REL_TYPE:
                raise RuntimeError(f"Unexpected relationship type for {target}")
            target_to_rel_id[target] = relationship.get("Id")
    expected_targets = {f"media/{name}" for name in TARGETS}
    if set(target_to_rel_id) != expected_targets:
        raise RuntimeError(f"Missing expected image relationships: {sorted(expected_targets - set(target_to_rel_id))}")

    vector_rel_ids: dict[str, str] = {}
    for index, (png_name, svg_name) in enumerate(TARGETS.items(), start=1):
        vector_id = f"rIdVectorFigure{index}"
        if vector_id in existing_ids:
            raise RuntimeError(f"Relationship ID already exists: {vector_id}")
        ET.SubElement(root, f"{{{REL_NS}}}Relationship", {
            "Id": vector_id,
            "Type": IMAGE_REL_TYPE,
            "Target": f"media/{svg_name}",
        })
        vector_rel_ids[target_to_rel_id[f"media/{png_name}"]] = vector_id
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), vector_rel_ids


def update_document_blips(data: bytes, vector_rel_ids: dict[str, str]) -> bytes:
    root = ET.fromstring(data)
    matched: set[str] = set()
    for blip in root.findall(f".//{{{DRAWING_NS}}}blip"):
        fallback_rel_id = blip.get(f"{{{DOC_REL_NS}}}embed")
        if fallback_rel_id not in vector_rel_ids:
            continue
        ext_list = blip.find(f"{{{DRAWING_NS}}}extLst")
        if ext_list is None:
            ext_list = ET.SubElement(blip, f"{{{DRAWING_NS}}}extLst")
        if any(child.get("uri") == SVG_BLIP_EXT_URI for child in ext_list.findall(f"{{{DRAWING_NS}}}ext")):
            raise RuntimeError(f"SVG extension already present for fallback {fallback_rel_id}")
        ext = ET.SubElement(ext_list, f"{{{DRAWING_NS}}}ext", {"uri": SVG_BLIP_EXT_URI})
        ET.SubElement(ext, f"{{{ASVG_NS}}}svgBlip", {f"{{{DOC_REL_NS}}}embed": vector_rel_ids[fallback_rel_id]})
        matched.add(fallback_rel_id)
    if matched != set(vector_rel_ids):
        raise RuntimeError(f"Could not find expected image blips: {sorted(set(vector_rel_ids) - matched)}")
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
        data = (args.svg_directory / source_name).read_bytes()
        ET.fromstring(data)
        svg_bytes[source_name] = data

    with zipfile.ZipFile(args.source_docx, "r") as source_zip:
        source_infos = source_zip.infolist()
        source_names = [info.filename for info in source_infos]
        required = {CONTENT_TYPES_PART, RELS_PART, DOCUMENT_PART, *{f"word/media/{name}" for name in TARGETS}}
        missing = required - set(source_names)
        if missing:
            raise RuntimeError(f"Source DOCX lacks required parts: {sorted(missing)}")
        source_data = {info.filename: source_zip.read(info.filename) for info in source_infos}
        updated_rels, vector_rel_ids = update_relationships(source_data[RELS_PART])
        updated_document = update_document_blips(source_data[DOCUMENT_PART], vector_rel_ids)
        updated_content_types = update_content_types(source_data[CONTENT_TYPES_PART])

        args.output_docx.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output_docx, "w") as output_zip:
            for info in source_infos:
                name = info.filename
                data = ({
                    CONTENT_TYPES_PART: updated_content_types,
                    RELS_PART: updated_rels,
                    DOCUMENT_PART: updated_document,
                }).get(name, source_data[name])
                write_entry(output_zip, info, name, data)
            for svg_name, data in svg_bytes.items():
                svg_info = zipfile.ZipInfo(f"word/media/{svg_name}")
                svg_info.compress_type = zipfile.ZIP_DEFLATED
                output_zip.writestr(svg_info, data)

    modified_parts = {CONTENT_TYPES_PART, RELS_PART, DOCUMENT_PART}
    with zipfile.ZipFile(args.output_docx, "r") as output_zip:
        if output_zip.testzip() is not None:
            raise RuntimeError("DOCX ZIP integrity test failed")
        expected_names = set(source_names) | {f"word/media/{svg_name}" for svg_name in TARGETS.values()}
        if set(output_zip.namelist()) != expected_names:
            raise RuntimeError("Output package entry list differs from the expected SVG embedding")
        for name in source_names:
            if name not in modified_parts and digest(output_zip.read(name)) != digest(source_data[name]):
                raise RuntimeError(f"Unexpected content change in {name}")
        rels_root = ET.fromstring(output_zip.read(RELS_PART))
        relationships = {entry.get("Id"): entry.get("Target") for entry in rels_root.findall(f"{{{REL_NS}}}Relationship")}
        for index, svg_name in enumerate(TARGETS.values(), start=1):
            vector_id = f"rIdVectorFigure{index}"
            if relationships.get(vector_id) != f"media/{svg_name}":
                raise RuntimeError(f"SVG relationship missing for {svg_name}")
            if output_zip.read(f"word/media/{svg_name}") != svg_bytes[svg_name]:
                raise RuntimeError(f"SVG payload mismatch for {svg_name}")
        document_root = ET.fromstring(output_zip.read(DOCUMENT_PART))
        embedded_vectors = {element.get(f"{{{DOC_REL_NS}}}embed") for element in document_root.findall(f".//{{{ASVG_NS}}}svgBlip")}
        expected_vector_ids = {f"rIdVectorFigure{n}" for n in range(1, len(TARGETS) + 1)}
        if embedded_vectors != expected_vector_ids:
            raise RuntimeError("DOCX drawing XML does not embed exactly the four expected SVGs")
        content_types_root = ET.fromstring(output_zip.read(CONTENT_TYPES_PART))
        if not any(item.get("Extension", "").lower() == "svg" and item.get("ContentType") == "image/svg+xml" for item in content_types_root.findall(f"{{{PKG_NS}}}Default")):
            raise RuntimeError("SVG content type is missing")

    print(f"output={args.output_docx}")
    print("embedded_svg=fig1.svg,fig2.svg,fig3.svg,fig4.svg")
    print("png_fallbacks=image15.png,image16.png,image17.png,image18.png")
    print("unchanged_parts_verified=all_non_target_parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
