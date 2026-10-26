# ruff: noqa: E501  # long lines hold verbatim paper excerpts / hashes / Chinese UI strings
"""Extract and verify EvidenceSpans for the selected benchmark paper.

Paper: Bleichrodt et al. 2024, Royal Society Open Science 11(7):240248,
DOI 10.1098/rsos.240248 (formal published version, PMC render of the
publisher VOR PDF, CC-BY 4.0).

For every claim we locate an exact excerpt in the extracted page text of the
formal PDF and record: physical page (1-based), printed page label, section,
raw excerpt, start/end offsets into the whitespace-normalized page text,
SHA-256 of raw excerpt, SHA-256 of normalized excerpt, SHA-256 of source PDF,
and the evidence role. `verify` mode re-reads the PDF and recomputes every
hash from scratch; it fails loudly if any span no longer matches.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader

PDF_PATH = Path(__file__).resolve().parent.parent / "paper" / "rsos240248_pmc.pdf"
OUT_PATH = Path(__file__).resolve().parent / "kg_evidence_spans.json"

# (claim_id, section, evidence_role, excerpt)  Excerpts are matched against the
# whitespace-normalized page text of the formal PDF.
CLAIMS: list[tuple[str, str, str, str]] = [
    (
        "work.identity",
        "Title page",
        "IDENTITY",
        "Evaluating the forecasting performance of ensemble sub-epidemic frameworks and other time series models for the 2022\u20132023 mpox epidemic",
    ),
    (
        "work.authors",
        "Title page",
        "IDENTITY",
        "Amanda Bleichrodt, Ruiyan Luo, Alexander Kirpich and Gerardo Chowell",
    ),
    (
        "work.venue_doi",
        "Title page",
        "IDENTITY",
        "https://doi.org/10.1098/rsos.240248",
    ),
    (
        "work.venue_citation",
        "Title page",
        "IDENTITY",
        "R. Soc. Open Sci. 11: 240248",
    ),
    (
        "data.sources",
        "Methods - Data source and preparation",
        "METHOD",
        "from the Centers for Disease Control and Prevention (CDC) [30] and the Our World in Data (OWID) GitHub page [18] on 9 and 15 August 2023",
    ),
    (
        "data.weekly_aggregation",
        "Methods - Data source and preparation",
        "METHOD",
        "We aggregated cases to the weekly level to ensure an adequate number of cases for the forecasting process",
    ),
    (
        "protocol.models_12",
        "Methods - Model calibration and forecasting strategy",
        "METHOD",
        "Our analysis included 12 models in total: the n-sub-epidemic framework",
    ),
    (
        "protocol.rolling_forecasts",
        "Methods - Model calibration and forecasting strategy",
        "METHOD",
        "approximately 112 retrospective weekly sequential one- to four-week forecasts, with forecasts spanning from the week of 21 July 2022 to the week of 23 February 2023",
    ),
    (
        "protocol.calibration_11w",
        "Methods - Model calibration and forecasting strategy",
        "METHOD",
        "We primarily employed 11-week calibration periods for each location",
    ),
    (
        "model.subepidemic_equation",
        "Methods - Sub-epidemic wave framework",
        "METHOD",
        "The mathematical building block for the spatial-wave model is the same three-parameter generalized logistic growth model",
    ),
    (
        "model.arima_autoarima",
        "Methods - ARIMA models",
        "METHOD",
        "We used the auto.arima function from the R",
    ),
    (
        "model.arima_truncate_zero",
        "Methods - ARIMA models",
        "METHOD",
        "Any negative predicted values were truncated at zero",
    ),
    (
        "model.gam_mgcv",
        "Methods - Generalized additive models",
        "METHOD",
        "We used the GAM function in the R",
    ),
    (
        "metrics.set",
        "Methods - Performance metrics",
        "METHOD",
        "MAE, m.s.e., 95% PI coverage and WIS",
    ),
    (
        "baseline.arima",
        "Methods - Skill scores and Winkler scores",
        "METHOD",
        "We selected the ARIMA model for the baseline model",
    ),
    (
        "uncertainty.bootstrap300",
        "Methods - Parametric bootstrapping",
        "METHOD",
        "we used 300 bootstrap realizations to thoroughly characterize the parameter and forecasting uncertainty",
    ),
    (
        "limitation.short_calibration",
        "Discussion",
        "LIMITATION",
        "Our study is not exempt from limitations.",
    ),
    (
        "limitation.short_term_only",
        "Discussion",
        "LIMITATION",
        "current models are not responsive to seasonal or event-specific risk behaviours, which limits their application to short-term forecasts",
    ),
]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_ws(text: str) -> str:
    """Whitespace-collapse plus bracket-padding removal (PDF extraction pads
    citation markers like ``[ 30]`` with spaces)."""
    out = re.sub(r"\s+", " ", text)
    out = re.sub(r"\[\s+", "[", out)
    out = re.sub(r"\s+\]", "]", out)
    # dehyphenate line-break splits (\"general- ized\" -> \"generalized\")
    out = re.sub(r"(?<=[A-Za-z])- (?=[a-z])", "", out)
    return out


def detect_printed_label(raw_page_text: str, physical: int) -> str:
    """RSOS paginates by article number; pages carry a small running number."""
    tail = normalize_ws(raw_page_text)[-120:]
    m = re.search(r"\b(\d{1,2})\s*$", tail)
    if m and abs(int(m.group(1)) - physical) <= 1:
        return m.group(1)
    return f"unnumbered-article-pagination(physical={physical})"


def build() -> dict[str, Any]:
    pdf_bytes = PDF_PATH.read_bytes()
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    reader = PdfReader(str(PDF_PATH))
    pages_raw = [(p.extract_text() or "") for p in reader.pages]
    pages_norm = [normalize_ws(t) for t in pages_raw]
    spans: list[dict[str, Any]] = []
    for claim_id, section, role, excerpt in CLAIMS:
        needle = normalize_ws(excerpt)
        hit = None
        for idx, page_text in enumerate(pages_norm):
            start = page_text.find(needle)
            if start >= 0:
                hit = (idx, start)
                break
        if hit is None:
            raise SystemExit(f"CLAIM NOT LOCATED in formal PDF: {claim_id}: {needle[:80]}")
        page_idx, start = hit
        end = start + len(needle)
        raw_excerpt = pages_norm[page_idx][start:end]
        spans.append(
            {
                "claim_id": claim_id,
                "evidence_role": role,
                "section": section,
                "physical_page": page_idx + 1,
                "printed_page_label": detect_printed_label(pages_raw[page_idx], page_idx + 1),
                "page_text_offset_start": start,
                "page_text_offset_end": end,
                "offset_basis": "pypdf extract_text, normalized (\\\\s+ collapsed; [] padding removed; line-break hyphenation removed)",
                "excerpt": raw_excerpt,
                "excerpt_sha256": sha256_text(raw_excerpt),
                "normalized_excerpt_sha256": sha256_text(normalize_ws(raw_excerpt)),
                "source_pdf_sha256": pdf_sha,
                "source_pdf_file": PDF_PATH.name,
                "source_pdf_identity": (
                    "Formal published version (VOR) of R. Soc. Open Sci. 11(7):240248, "
                    "obtained via Europe PMC PMC11285753 render of the publisher PDF, CC-BY 4.0"
                ),
            }
        )
    return {
        "schema_version": "kg.evidence_spans.v1",
        "work": {
            "title": CLAIMS[0][3],
            "doi": "10.1098/rsos.240248",
            "venue": "Royal Society Open Science",
            "volume": "11",
            "issue": "7",
            "article_number": "240248",
            "published": "2024-07",
            "license": "CC-BY 4.0",
            "publisher_url": "https://royalsocietypublishing.org/doi/10.1098/rsos.240248",
            "pmcid": "PMC11285753",
            "pmid": "39076375",
        },
        "n_pages": len(pages_raw),
        "spans": spans,
    }


def verify(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pdf_bytes = PDF_PATH.read_bytes()
    if hashlib.sha256(pdf_bytes).hexdigest() != doc["spans"][0]["source_pdf_sha256"]:
        errors.append("source PDF sha256 mismatch")
        return errors
    reader = PdfReader(str(PDF_PATH))
    pages_norm = [normalize_ws(p.extract_text() or "") for p in reader.pages]
    for span in doc["spans"]:
        page_text = pages_norm[span["physical_page"] - 1]
        excerpt = page_text[span["page_text_offset_start"] : span["page_text_offset_end"]]
        if excerpt != span["excerpt"]:
            errors.append(f"{span['claim_id']}: excerpt mismatch at recorded offsets")
            continue
        if sha256_text(excerpt) != span["excerpt_sha256"]:
            errors.append(f"{span['claim_id']}: excerpt sha256 mismatch")
        if sha256_text(normalize_ws(excerpt)) != span["normalized_excerpt_sha256"]:
            errors.append(f"{span['claim_id']}: normalized excerpt sha256 mismatch")
    return errors


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        errors = verify(doc)
        if errors:
            print(json.dumps({"verify": "FAILED", "errors": errors}, indent=2))
            raise SystemExit(1)
        print(json.dumps({"verify": "OK", "spans": len(doc["spans"])}))
        return
    doc = build()
    errors = verify(doc)
    if errors:
        raise SystemExit(f"self-verify failed: {errors}")
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"built": str(OUT_PATH), "spans": len(doc["spans"]), "pages": doc["n_pages"]}))


if __name__ == "__main__":
    main()
