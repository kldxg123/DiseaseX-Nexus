from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824")


def restore(target: Path, source: Path, names: list[str]) -> None:
    with zipfile.ZipFile(source) as archive:
        replacements = {name: archive.read(name) for name in names}
    with TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        with zipfile.ZipFile(target) as archive:
            archive.extractall(temp)
        for name, data in replacements.items():
            member = temp / name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(data)
        rebuilt = target.with_suffix(".restored.docx")
        with zipfile.ZipFile(rebuilt, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member in sorted(temp.rglob("*")):
                if member.is_file():
                    archive.write(member, member.relative_to(temp).as_posix())
        shutil.move(rebuilt, target)


restore(
    ROOT / "DiseaseX_Nexus_NC_Manuscript_v6_20260824.docx",
    ROOT / "DiseaseX_Nexus_NC_Manuscript_v4_20260824.docx",
    ["word/media/image1.jpeg", "word/media/image3.jpeg"],
)
restore(
    ROOT / "Supplementary" / "Supplementary_Information_Updated_v3_20260824.docx",
    ROOT / "Supplementary" / "Supplementary_Information_Updated_20260824.docx",
    ["word/media/image1.jpeg", "word/media/image3.jpeg"],
)
print("restored_original_figure_1_and_3=PASS")
