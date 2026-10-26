from __future__ import annotations


def detect_case_stimuli(feature_row: dict) -> list[dict]:
    stimuli = []
    if float(feature_row.get("growth_7d", 0.0) or 0.0) > 0.2:
        stimuli.append({"type": "case_stimulus", "reason": "new_cases_7d_growth_high"})
    if feature_row.get("data_quality_flags"):
        stimuli.append({"type": "data_quality_stimulus", "reason": "case_data_quality_flags"})
    return stimuli
