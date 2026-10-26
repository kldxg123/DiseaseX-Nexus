from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path


ZIP_PATH = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC投稿完善_20260824_原Fig1Fig3_英文Supplementary终版.zip")
ROOT_NAME = "NC_Submission_Final_20260824_OriginalFig1Fig3_EnglishSupplement"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


with zipfile.ZipFile(ZIP_PATH) as archive:
    assert archive.testzip() is None
    names = set(archive.namelist())
    sums_name = f"{ROOT_NAME}/SHA256SUMS.txt"
    validation_name = f"{ROOT_NAME}/PACKAGE_VALIDATION_20260824_v3.json"
    sums = archive.read(sums_name).decode("utf-8")
    checked = 0
    for line in sums.splitlines():
        expected, relative = line.split("  ", 1)
        member = f"{ROOT_NAME}/{relative}"
        assert member in names, member
        assert digest(archive.read(member)) == expected, member
        checked += 1
    validation = json.loads(archive.read(validation_name))
    assert validation["status"] == "PASS"
    assert checked == validation["sha256_entry_count"]
    forbidden = {
        "Figure_6_Retrospective_Multi_Pathogen_Evaluation.png",
        "Figure_6_Retrospective_Multi_Pathogen_Evaluation_v2.png",
        "Figure_6_Retrospective_Multi_Pathogen_Evaluation.svg",
        "Main_Figure_6_Retrospective_Multi_Pathogen_Evaluation.png",
    }
    assert not any(Path(name).name in forbidden for name in names)
    print(json.dumps({"zip_crc": "PASS", "manifest_entries_verified": checked, "validation_status": validation["status"], "forbidden_legacy_assets": 0, "zip_members": len(names)}, indent=2))
