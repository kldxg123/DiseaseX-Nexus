from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import DiseaseCountryInformationRecord


def build_3d_visualization_dataset(
    session: Session,
    *,
    disease: str,
    country: str,
    mode: str = "auto",
) -> dict[str, Any]:
    rows = session.scalars(
        select(DiseaseCountryInformationRecord)
        .where(DiseaseCountryInformationRecord.disease == disease.lower())
        .where(DiseaseCountryInformationRecord.country == country)
        .order_by(DiseaseCountryInformationRecord.created_at)
    ).all()
    if mode == "auto":
        mode = "metric" if any(row.metric_value is not None for row in rows) else "evidence_density"
    if mode == "metric":
        points = [
            {
                "x": row.iso_week or (row.event_date.isoformat() if row.event_date else None),
                "y": row.region_name or row.source_domain,
                "z": row.metric_value,
                "color": row.metric_name or row.information_type,
                "size": row.confidence,
                "source_domain": row.source_domain,
                "information_type": row.information_type,
                "cleaning_status": row.cleaning_status,
            }
            for row in rows
            if row.metric_value is not None
        ]
        z_label = "metric_value"
    elif mode == "cleaning_status":
        counter = Counter((row.source_domain, row.cleaning_status) for row in rows)
        points = [
            {
                "x": "records",
                "y": domain,
                "z": count,
                "color": status,
                "size": min(1.0, 0.2 + count / 10),
                "source_domain": domain,
                "cleaning_status": status,
            }
            for (domain, status), count in counter.items()
        ]
        z_label = "record_count"
    else:
        counter = Counter((row.source_domain, row.information_type) for row in rows)
        points = [
            {
                "x": "evidence",
                "y": domain,
                "z": count,
                "color": info_type,
                "size": min(1.0, 0.2 + count / 10),
                "source_domain": domain,
                "information_type": info_type,
            }
            for (domain, info_type), count in counter.items()
        ]
        z_label = "evidence_count_not_case_count"
    return {
        "disease": disease.lower(),
        "country": country,
        "mode": mode,
        "points": points,
        "axes": {"x": "time_or_mode", "y": "region_or_source_domain", "z": z_label},
        "color_field": "color",
        "hover_fields": ["source_domain", "information_type", "cleaning_status"],
        "warnings": [
            "Visualization is exploratory and requires human review.",
            "Evidence density mode is not a case count." if mode != "metric" else "Metric mode uses extracted numeric values only.",
        ],
    }
