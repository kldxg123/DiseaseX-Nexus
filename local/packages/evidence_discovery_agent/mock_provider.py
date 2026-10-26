from __future__ import annotations

import json
from pathlib import Path

from evidence_discovery_agent.evidence_parser import parse_evidence_documents
from evidence_discovery_agent.types import DiseaseTaskDefinition, EvidenceDocument

DEFAULT_FIXTURE_DIR = Path("tests/fixtures/evidence_discovery")


class MockEvidenceProvider:
    def __init__(self, fixture_dir: str | Path = DEFAULT_FIXTURE_DIR):
        self.fixture_dir = Path(fixture_dir)
        self.network_accessed = False

    def discover(self, task: DiseaseTaskDefinition) -> list[EvidenceDocument]:
        documents = self.load_all()
        disease = task.disease.lower()
        target = task.prediction_target.lower()
        filtered = [
            document
            for document in documents
            if document.disease in {disease, "generic"} and _target_matches(target, document.target)
        ]
        return filtered or [document for document in documents if document.disease == "generic"]

    def load_all(self) -> list[EvidenceDocument]:
        documents: list[EvidenceDocument] = []
        for path in sorted(self.fixture_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            documents.extend(parse_evidence_documents(payload))
        return documents


def _target_matches(task_target: str, document_target: str) -> bool:
    if not document_target:
        return True
    doc = document_target.lower()
    if doc == task_target:
        return True
    if "trend" in task_target and "trend" in doc:
        return True
    if "outbreak" in task_target and "outbreak" in doc:
        return True
    return doc == "generic"
