from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path


ZIP_PATH = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC投稿完善_20260824_主文与Supplementary英文图终版v3.zip")
ROOT = "NC_Submission_Final_v3_20260824_EnglishFiguresEmbedded"
MAIN_DOCX = f"{ROOT}/DiseaseX_Nexus_NC_Manuscript_v7_20260824.docx"
SI_DOCX = f"{ROOT}/Supplementary/Supplementary_Information_Updated_v4_20260824.docx"
FIGURE_DIR = f"{ROOT}/Supplementary/SI_Figures"
FIGURES = {
    "main": {
        "word/media/image6.png": "Supplementary_Figure_1_HK_Flu_Main_RMSE.png",
        "word/media/image7.png": "Supplementary_Figure_2_HK_Flu_Horizon_RMSE.png",
        "word/media/image8.png": "Supplementary_Figure_3_HK_Flu_PostCOVID_RMSE.png",
        "word/media/image9.png": "Supplementary_Figure_4_Governed_Tracks.png",
    },
    "supplementary": {
        "word/media/image4.png": "Supplementary_Figure_1_HK_Flu_Main_RMSE.png",
        "word/media/image5.png": "Supplementary_Figure_2_HK_Flu_Horizon_RMSE.png",
        "word/media/image6.png": "Supplementary_Figure_3_HK_Flu_PostCOVID_RMSE.png",
        "word/media/image8.png": "Supplementary_Figure_4_Governed_Tracks.png",
    },
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


with zipfile.ZipFile(ZIP_PATH) as outer:
    assert outer.testzip() is None
    names = set(outer.namelist())
    validation = json.loads(outer.read(f"{ROOT}/PACKAGE_VALIDATION_20260824_v5.json"))
    assert validation["status"] == "PASS"
    checked = 0
    for line in outer.read(f"{ROOT}/SHA256SUMS.txt").decode("utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        member = f"{ROOT}/{relative}"
        assert member in names
        assert digest(outer.read(member)) == expected
        checked += 1

    document_members = {"main": MAIN_DOCX, "supplementary": SI_DOCX}
    embedded_checks = {}
    for document_name, document_member in document_members.items():
        with zipfile.ZipFile(io.BytesIO(outer.read(document_member))) as docx:
            embedded_checks[document_name] = {}
            for media_member, filename in FIGURES[document_name].items():
                embedded = docx.read(media_member)
                external = outer.read(f"{FIGURE_DIR}/{filename}")
                assert embedded == external
                embedded_checks[document_name][filename] = digest(embedded)

    forbidden_versions = ("Manuscript_v6", "Supplementary_Information_Updated_v3", "Content_Manifest_Updated_v4")
    assert not any(any(token in name for token in forbidden_versions) for name in names)
    print(json.dumps({"zip_crc": "PASS", "manifest_entries_verified": checked, "main_figures_byte_identical": len(embedded_checks["main"]), "supplementary_figures_byte_identical": len(embedded_checks["supplementary"]), "figure_sha256": embedded_checks, "old_versions_present": False, "zip_members": len(names)}, indent=2))
