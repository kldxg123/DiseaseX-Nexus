from __future__ import annotations

from evidence_discovery_agent.types import DiseaseTaskDefinition


def build_disease_task(
    *,
    disease: str,
    country: str,
    prediction_target: str,
    horizon_days: int = 28,
    data_modality: str = "weekly_surveillance",
) -> DiseaseTaskDefinition:
    return DiseaseTaskDefinition(
        disease=str(disease).strip().lower(),
        country=str(country).strip(),
        prediction_target=str(prediction_target).strip().lower(),
        horizon_days=int(horizon_days),
        data_modality=str(data_modality).strip().lower(),
        limitations=[
            "Readiness task only; recommendations require rolling backtest before use.",
            "Fixture/mock evidence only; no live literature discovery was performed.",
        ],
    )
