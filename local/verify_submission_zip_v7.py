from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path


ZIP_PATH = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC投稿完善_20260824_英文Supplementary图已嵌入终版v2.zip")
ROOT = "NC_Submission_Final_v2_20260824_EnglishSupplementEmbedded"
SI_DOCX = f"{ROOT}/Supplementary/Supplementary_Information_Updated_v4_20260824.docx"
FIGURE_DIR = f"{ROOT}/Supplementary/SI_Figures"
FIGURES = {
    "word/media/image4.png": "Supplementary_Figure_1_HK_Flu_Main_RMSE.png",
    "word/media/image5.png": "Supplementary_Figure_2_HK_Flu_Horizon_RMSE.png",
    "word/media/image6.png": "Supplementary_Figure_3_HK_Flu_PostCOVID_RMSE.png",
    "word/media/image8.png": "Supplementary_Figure_4_Governed_Tracks.png",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


with zipfile.ZipFile(ZIP_PATH) as outer:
    assert outer.testzip() is None
    names = set(outer.namelist())
    validation = json.loads(outer.read(f"{ROOT}/PACKAGE_VALIDATION_20260824_v4.json"))
    assert validation["status"] == "PASS"
    checked = 0
    for line in outer.read(f"{ROOT}/SHA256SUMS.txt").decode("utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        member = f"{ROOT}/{relative}"
        assert member in names
        assert digest(outer.read(member)) == expected
        checked += 1
    docx_bytes = outer.read(SI_DOCX)
    figure_checks = {}
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as docx:
        for media_member, filename in FIGURES.items():
            embedded = docx.read(media_member)
            external = outer.read(f"{FIGURE_DIR}/{filename}")
            assert embedded == external
            figure_checks[filename] = digest(embedded)
    assert not any("Supplementary_Information_Updated_v3" in name for name in names)
    print(json.dumps({"zip_crc": "PASS", "manifest_entries_verified": checked, "embedded_figures_byte_identical": len(figure_checks), "figure_sha256": figure_checks, "old_v3_supplementary_present": False, "zip_members": len(names)}, indent=2))
