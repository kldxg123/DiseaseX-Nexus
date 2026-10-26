
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


class PortableLogisticModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        state = payload["model_state"]
        self.feature_names = list(state["feature_names"])
        self.means = [float.fromhex(x) for x in state["means_hex"]]
        self.stds = [float.fromhex(x) for x in state["stds_hex"]]
        self.weights = [float.fromhex(x) for x in state["weights_hex"]]
        self.bias = float.fromhex(state["bias_hex"])
        self.threshold = float.fromhex(state["decision_threshold_hex"])

    def transform(self, features: dict[str, Any]) -> list[float]:
        out: list[float] = []
        for i, name in enumerate(self.feature_names):
            value = features.get(name)
            if value is None:
                value = self.means[i]
            value = float(value)
            if not math.isfinite(value):
                value = self.means[i]
            out.append((value - self.means[i]) / self.stds[i])
        return out

    def predict_proba(self, features: dict[str, Any]) -> float:
        x = self.transform(features)
        return sigmoid(self.bias + sum(w * xv for w, xv in zip(self.weights, x)))

    def predict_label(self, features: dict[str, Any]) -> bool:
        return self.predict_proba(features) >= self.threshold


def load_portable_model(path: str | Path) -> PortableLogisticModel:
    return PortableLogisticModel(json.loads(Path(path).read_text(encoding="utf-8")))
