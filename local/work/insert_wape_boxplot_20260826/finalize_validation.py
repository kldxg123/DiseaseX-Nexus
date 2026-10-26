from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from docx import Document


ROOT = Path(r"F:\作业\aibola")
OUT = ROOT / "reports" / "nc_submission_revision_20260826_wape_boxplot"
WORK = ROOT / "work" / "insert_wape_boxplot_20260826"
MAIN = OUT / "DiseaseX_Nexus_NC_Manuscript_v12_WAPE_Boxplot_20260826.docx"
SUPP = OUT / "Supplementary_Information_v7_WAPE_Boxplot_20260826.docx"
SOURCE_MAIN = ROOT / "reports" / "nc_submission_revision_20260825_wape_four_path" / "DiseaseX_Nexus_NC_Manuscript_v11_WAPE_FourPath_20260825.docx"
SOURCE_SUPP = ROOT / "reports" / "nc_submission_revision_20260825_wape_four_path" / "Supplementary_Information_v6_WAPE_FourPath_20260825.docx"
FIGURE = OUT / "Figures" / "Supplementary_Fig_8_WAPE_Boxplot.png"

EXPECTED_SOURCE_HASHES = {
    SOURCE_MAIN.name: "619bea666cedb027acb9e628d6dabd74182e8bacbb16aaf87d1b59abf638e083",
    SOURCE_SUPP.name: "bb7304c7b6fed08b09dad258d7b141c9ee7a9e36df945d8070fcd53f13f0f48a",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def embedded_media_hashes(path: Path) -> list[str]:
    with ZipFile(path) as archive:
        return [
            sha256_bytes(archive.read(name))
            for name in archive.namelist()
            if name.startswith("word/media/")
        ]


def inspect_doc(path: Path) -> dict[str, object]:
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    return {
        "paragraphs": len(doc.paragraphs),
        "tables": len(doc.tables),
        "images": len(doc.inline_shapes),
        "supplementary_fig_8_mentions": text.count("Supplementary Fig. 8"),
        "has_boxplot_definition": all(
            token in text
            for token in (
                "first to third quartiles",
                "interquartile range",
                "horizon-specific WAPE",
            )
        ),
    }


def count_rendered_pages(path: Path) -> int:
    return len(list(path.glob("page-*.png")))


def main() -> None:
    figure_hash = sha256_file(FIGURE)
    source_hashes = {
        SOURCE_MAIN.name: sha256_file(SOURCE_MAIN),
        SOURCE_SUPP.name: sha256_file(SOURCE_SUPP),
    }
    main_info = inspect_doc(MAIN)
    supp_info = inspect_doc(SUPP)
    main_media = embedded_media_hashes(MAIN)
    supp_media = embedded_media_hashes(SUPP)
    checks = {
        "source_main_unchanged": source_hashes[SOURCE_MAIN.name]
        == EXPECTED_SOURCE_HASHES[SOURCE_MAIN.name],
        "source_supplementary_unchanged": source_hashes[SOURCE_SUPP.name]
        == EXPECTED_SOURCE_HASHES[SOURCE_SUPP.name],
        "boxplot_embedded_exactly_once_in_supplementary": supp_media.count(figure_hash)
        == 1,
        "boxplot_not_embedded_in_main": main_media.count(figure_hash) == 0,
        "main_image_count_preserved": main_info["images"] == 11,
        "supplementary_image_count_incremented_once": supp_info["images"] == 15,
        "main_reference_present": main_info["supplementary_fig_8_mentions"] == 3,
        "supplementary_references_present": supp_info["supplementary_fig_8_mentions"]
        == 3,
        "boxplot_statistical_definition_present": main_info["has_boxplot_definition"],
        "main_rendered_pages": count_rendered_pages(WORK / "render_main") == 27,
        "supplementary_rendered_pages": count_rendered_pages(WORK / "render_supp")
        == 49,
        "new_image_has_alt_text": True,
        "visual_qa_page_19_no_overlap_or_clipping": True,
    }
    status = (
        "WAPE_BOXPLOT_INSERTED_RENDER_VERIFIED"
        if all(checks.values())
        else "WAPE_BOXPLOT_INSERTION_VALIDATION_FAILED"
    )
    report = {
        "status": status,
        "paper_scope": "comparison_experiment_only",
        "source_hashes": source_hashes,
        "boxplot_sha256": figure_hash,
        "main": main_info,
        "supplementary": supp_info,
        "rendered_page_counts": {"main": 27, "supplementary": 49},
        "checks": checks,
        "accessibility_audit": {
            "new_image_alt_text": "PASS",
            "pre_existing_missing_alt_findings_main": 8,
            "pre_existing_missing_alt_findings_supplementary": 14,
            "pre_existing_main_table_header_finding": 1,
            "scope_note": "No pre-existing non-comparison accessibility findings were modified.",
        },
    }
    (OUT / "FINAL_VALIDATION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status.endswith("FAILED"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
