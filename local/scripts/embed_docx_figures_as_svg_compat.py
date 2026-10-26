"""Embed four SVG figures in a DOCX using Office-compatible numeric rIds."""

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
IMAGE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
CONTENT_TYPES_PART = "[Content_Types].xml"
RELS_PART = "word/_rels/document.xml.rels"
DOCUMENT_PART = "word/document.xml"
TARGETS = {
    "image15.png": "fig1.svg",
    "image16.png": "fig2.svg",
    "image17.png": "fig3.svg",
    "image18.png": "fig4.svg",
}

ET.register_namespace("", PKG_NS)
ET.register_namespace("a", DRAWING_NS)
ET.register_namespace("r", DOC_REL_NS)
ET.register_namespace("asvg", ASVG_NS)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_entry(out_zip: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes) -> None:
    out_zip.writestr(copy.copy(info), data, compress_type=info.compress_type)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_docx", type=Path)
    parser.add_argument("svg_directory", type=Path)
    parser.add_argument("output_docx", type=Path)
    args = parser.parse_args()
    if args.output_docx.exists():
        raise FileExistsError(args.output_docx)

    svgs = {}
    for svg_name in TARGETS.values():
        data = (args.svg_directory / svg_name).read_bytes()
        ET.fromstring(data)
        svgs[svg_name] = data

    with zipfile.ZipFile(args.source_docx, "r") as zin:
        infos = zin.infolist()
        names = [item.filename for item in infos]
        data = {item.filename: zin.read(item.filename) for item in infos}
    required = {CONTENT_TYPES_PART, RELS_PART, DOCUMENT_PART, *{f"word/media/{name}" for name in TARGETS}}
    if required - set(names):
        raise RuntimeError(f"Missing source parts: {sorted(required - set(names))}")

    rel_root = ET.fromstring(data[RELS_PART])
    fallback_rids = {}
    existing_ids = {rel.get("Id") for rel in rel_root.findall(f"{{{REL_NS}}}Relationship")}
    for rel in rel_root.findall(f"{{{REL_NS}}}Relationship"):
        if rel.get("Target") in {f"media/{name}" for name in TARGETS}:
            if rel.get("Type") != IMAGE_REL_TYPE:
                raise RuntimeError(f"Unexpected relation type: {rel.get('Target')}")
            fallback_rids[rel.get("Target")] = rel.get("Id")
    expected_fallbacks = {f"media/{name}" for name in TARGETS}
    if set(fallback_rids) != expected_fallbacks:
        raise RuntimeError("Expected fallback image relationships were not found")

    vector_rids = {}
    max_numeric_rid = max(int(item[3:]) for item in existing_ids if item and item.startswith("rId") and item[3:].isdigit())
    for offset, (png_name, svg_name) in enumerate(TARGETS.items(), start=1):
        vector_rid = f"rId{max_numeric_rid + offset}"
        ET.SubElement(rel_root, f"{{{REL_NS}}}Relationship", {
            "Id": vector_rid,
            "Type": IMAGE_REL_TYPE,
            "Target": f"media/{svg_name}",
        })
        vector_rids[fallback_rids[f"media/{png_name}"]] = vector_rid
    updated_rels = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)

    doc_root = ET.fromstring(data[DOCUMENT_PART])
    matched = set()
    for blip in doc_root.findall(f".//{{{DRAWING_NS}}}blip"):
        fallback_rid = blip.get(f"{{{DOC_REL_NS}}}embed")
        if fallback_rid not in vector_rids:
            continue
        ext_list = blip.find(f"{{{DRAWING_NS}}}extLst")
        if ext_list is None:
            ext_list = ET.SubElement(blip, f"{{{DRAWING_NS}}}extLst")
        ext = ET.SubElement(ext_list, f"{{{DRAWING_NS}}}ext", {"uri": SVG_BLIP_EXT_URI})
        ET.SubElement(ext, f"{{{ASVG_NS}}}svgBlip", {f"{{{DOC_REL_NS}}}embed": vector_rids[fallback_rid]})
        matched.add(fallback_rid)
    if matched != set(vector_rids):
        raise RuntimeError("Expected drawing blips were not found")
    updated_document = ET.tostring(doc_root, encoding="utf-8", xml_declaration=True)

    content_root = ET.fromstring(data[CONTENT_TYPES_PART])
    svg_default = next((item for item in content_root.findall(f"{{{PKG_NS}}}Default") if item.get("Extension", "").lower() == "svg"), None)
    if svg_default is None:
        svg_default = ET.SubElement(content_root, f"{{{PKG_NS}}}Default", {"Extension": "svg", "ContentType": "image/svg+xml"})
    else:
        svg_default.set("ContentType", "image/svg+xml")
    updated_content_types = ET.tostring(content_root, encoding="utf-8", xml_declaration=True)

    args.output_docx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output_docx, "w") as zout:
        replacements = {CONTENT_TYPES_PART: updated_content_types, RELS_PART: updated_rels, DOCUMENT_PART: updated_document}
        for info in infos:
            write_entry(zout, info, replacements.get(info.filename, data[info.filename]))
        for svg_name, svg_data in svgs.items():
            svg_info = zipfile.ZipInfo(f"word/media/{svg_name}")
            svg_info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(svg_info, svg_data)

    changed_parts = {CONTENT_TYPES_PART, RELS_PART, DOCUMENT_PART}
    with zipfile.ZipFile(args.output_docx, "r") as zout:
        if zout.testzip() is not None:
            raise RuntimeError("ZIP integrity test failed")
        expected_names = set(names) | {f"word/media/{svg_name}" for svg_name in TARGETS.values()}
        if set(zout.namelist()) != expected_names:
            raise RuntimeError("Unexpected DOCX package entries")
        for name in names:
            if name not in changed_parts and sha256(zout.read(name)) != sha256(data[name]):
                raise RuntimeError(f"Unexpected change in {name}")
        stored_svgs = {item.get(f"{{{DOC_REL_NS}}}embed") for item in ET.fromstring(zout.read(DOCUMENT_PART)).findall(f".//{{{ASVG_NS}}}svgBlip")}
        if stored_svgs != set(vector_rids.values()):
            raise RuntimeError("SVG drawing relationships are incomplete")
        for svg_name, svg_data in svgs.items():
            if zout.read(f"word/media/{svg_name}") != svg_data:
                raise RuntimeError(f"SVG payload mismatch: {svg_name}")

    print(f"output={args.output_docx}")
    print("embedded_svg=fig1.svg,fig2.svg,fig3.svg,fig4.svg")
    print("png_fallbacks_preserved=image15.png,image16.png,image17.png,image18.png")
    print("unchanged_parts_verified=all_non_target_parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
