# Milestone 2 Evidence Schema

The M2 M0 readiness layer uses local JSON and Markdown artifacts rather than ORM tables.

## Core Objects

- `DiseaseTaskDefinition`
- `EvidenceDocument`
- `FeatureCandidate`
- `ModelCandidate`
- `EvidenceRecord`
- `EvidenceDiscoveryReport`

## Required Fields

- disease
- country
- prediction_target
- horizon_days
- data_modality
- candidate_features
- candidate_models
- evaluation_metrics
- evidence_source
- evidence_quality_score
- applicability_notes
- limitations
- requires_human_review

## Safety

Evidence documents are short summaries only. Reports must not include full raw text, PDF content, HTML, response bodies, metadata JSON, provenance JSON, feature JSON, or lesson JSON.
