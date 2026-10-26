from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824")
SOURCE = ROOT / "DiseaseX_Nexus_NC_Manuscript_v6_20260824.docx"
TARGET = ROOT / "DiseaseX_Nexus_NC_Manuscript_v7_20260824.docx"
FIGURE_DIR = ROOT / "Supplementary" / "SI_Figures"

REPLACEMENTS = {
    "word/media/image6.png": FIGURE_DIR / "Supplementary_Figure_1_HK_Flu_Main_RMSE.png",
    "word/media/image7.png": FIGURE_DIR / "Supplementary_Figure_2_HK_Flu_Horizon_RMSE.png",
    "word/media/image8.png": FIGURE_DIR / "Supplementary_Figure_3_HK_Flu_PostCOVID_RMSE.png",
    "word/media/image9.png": FIGURE_DIR / "Supplementary_Figure_4_Governed_Tracks.png",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


with TemporaryDirectory() as temp_name:
    temp = Path(temp_name)
    with zipfile.ZipFile(SOURCE) as archive:
        archive.extractall(temp)
    for member, source in REPLACEMENTS.items():
        if not source.is_file():
            raise FileNotFoundError(source)
        (temp / member).write_bytes(source.read_bytes())
    with zipfile.ZipFile(TARGET, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in sorted(temp.rglob("*")):
            if member.is_file():
                archive.write(member, member.relative_to(temp).as_posix())

verification: dict[str, object] = {"source": str(SOURCE), "target": str(TARGET), "figures": {}}
with zipfile.ZipFile(TARGET) as archive:
    for member, source in REPLACEMENTS.items():
        embedded = archive.read(member)
        expected = source.read_bytes()
        verification["figures"][member] = {
            "external_file": source.name,
            "embedded_sha256": sha256(embedded),
            "external_sha256": sha256(expected),
            "byte_identical": embedded == expected,
        }

if not all(item["byte_identical"] for item in verification["figures"].values()):
    raise RuntimeError(json.dumps(verification, indent=2))

(ROOT / "MAIN_ENGLISH_FIGURE_EMBEDDING_V7.json").write_text(
    json.dumps(verification, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(verification, indent=2))
