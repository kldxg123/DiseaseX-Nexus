# Milestone 2 Safety Policy

M2 M0 is a readiness phase.

## Prohibited In M2 M0

- No public internet access.
- No Google Scholar, PubMed, arXiv, Semantic Scholar, WHO, CDC, ECDC, GDELT, search engine, translation, geocoding, LLM, paid API, or external API calls.
- No SSH, remote deployment, or Docker startup.
- No model training, Qwen/LoRA/STEP fine-tuning, production prediction, or automatic predictor replacement.
- No PDF downloading.
- No ORM or Alembic migration unless later explicitly required.

## Required

- Use local fixture/mock evidence only.
- Mark all recommendations as requiring human review.
- Rank models with implementation feasibility, validation feasibility, interpretability, and safety in mind.
- Do not automatically rank transformer or LLM approaches first.
