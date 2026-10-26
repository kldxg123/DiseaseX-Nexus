from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


DOCX = Path(r"F:\作业\aibola\论文材料包_20260813\01_论文说明文档\疫情自动获取系统_实验架构与预测结果论文材料.docx")
OUT = DOCX.with_name("DOCX_STRUCTURE_VERIFICATION.json")


required = {
    "[Content_Types].xml",
    "word/document.xml",
    "word/styles.xml",
    "word/settings.xml",
    "word/_rels/document.xml.rels",
}

with zipfile.ZipFile(DOCX, "r") as zf:
    names = set(zf.namelist())
    bad = zf.testzip()
    document_xml = zf.read("word/document.xml")
    root = ET.fromstring(document_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = root.findall(".//w:p", ns)
    tables = root.findall(".//w:tbl", ns)
    sections = root.findall(".//w:sectPr", ns)
    images = [n for n in names if n.startswith("word/media/")]
    text = "".join((t.text or "") for t in root.findall(".//w:t", ns))

checks = {
    "zip_crc": bad is None,
    "required_members": required.issubset(names),
    "document_xml_well_formed": root.tag.endswith("document"),
    "paragraph_count_positive": len(paragraphs) > 0,
    "table_count_positive": len(tables) > 0,
    "section_count_expected": len(sections) >= 5,
    "embedded_image_count_expected": len(images) >= 10,
    "contains_experiment_design": "对比实验设计" in text,
    "contains_project_architecture": "项目总体设计" in text,
    "contains_knowledge_graph": "预测模型知识图谱" in text,
    "contains_three_tracks": all(s in text for s in ("链路 A", "链路 B", "链路 C")),
    "contains_limitations": "复现实验的限制" in text,
    "replacement_character_absent": "�" not in text,
}

payload = {
    "file": DOCX.name,
    "size_bytes": DOCX.stat().st_size,
    "paragraph_count": len(paragraphs),
    "table_count": len(tables),
    "section_count": len(sections),
    "embedded_image_count": len(images),
    "checks": checks,
    "verdict": "PASS" if all(checks.values()) else "FAIL",
    "note": "Structural verification only. Automated DOCX-to-PDF rendering was unavailable in the current execution environment.",
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
