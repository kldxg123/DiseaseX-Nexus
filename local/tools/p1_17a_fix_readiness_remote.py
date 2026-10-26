from __future__ import annotations

from pathlib import Path


path = Path("src/epidemic_core/p1_10/diagnostics.py")
text = path.read_text(encoding="utf-8")
if '"0029_p1_17a_prediction_mvp"' not in text:
    old = '        ACTIVE_REQUIRED_REVISION,\n    }\n)'
    new = (
        '        ACTIVE_REQUIRED_REVISION,\n'
        '        "0028_review_raw_source_summaries",\n'
        '        "0029_p1_17a_prediction_mvp",\n'
        '    }\n)'
    )
    if old not in text:
        raise SystemExit("COMPATIBLE_REVISIONS_MARKER_NOT_FOUND")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
print("P1_17A_READINESS_COMPATIBILITY_PATCHED")
