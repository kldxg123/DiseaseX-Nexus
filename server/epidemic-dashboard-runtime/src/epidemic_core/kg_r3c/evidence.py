"""Phase C: page-level evidence span + assertion extraction (kg_r3c).

Machine extraction only (review_status='MACHINE_EXTRACTED'); every assertion
that enters scoring is bound to a concrete page-level EvidenceSpan
(document, physical page, char offsets, anchor text, source pdf sha256).
Metadata/title/abstract-only text NEVER creates scoring-eligible evidence.
"""
from __future__ import annotations

import json
import re

from .dbutil import sha256_text, stable_uuid
from .scoring import DISEASE_KEYWORDS, FAMILY_ALIASES, GENERIC_ID_TERMS

EXTRACTOR = "kg_r3c.evidence_extractor"
EXTRACTOR_VERSION = "kg_r3c.evidence.v1"

MAX_SPANS_PER_FAMILY_WORK = 3

PARAM_PATTERNS = [
    ("discount", re.compile(r"discount factor\s*(?:of|=|:)?\s*(0\.\d+)", re.I)),
    ("si_mean", re.compile(r"serial interval[^.]{0,80}?mean\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*day", re.I)),
    ("si_sd", re.compile(r"serial interval[^.]{0,120}?(?:sd|standard deviation)\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*day", re.I)),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def extract_from_page(text: str):
    """Yield (kind, family, disease_or_none, span_text, start, end, extra)."""
    low = text.lower()
    found = []
    for family, aliases in FAMILY_ALIASES.items():
        for alias in aliases:
            for m in re.finditer(re.escape(alias), low):
                start, end = m.span()
                span = _norm(text[max(0, start - 220): min(len(text), end + 220)])
                diseases = [d for d, kws in DISEASE_KEYWORDS.items()
                            if any(kw in low for kw in kws)]
                generic = any(t in low for t in GENERIC_ID_TERMS)
                found.append(("MODEL_MENTION", family, diseases, generic, span, start, end, {}))
                break  # one span per alias per page
    for pname, pat in PARAM_PATTERNS:
        m = pat.search(text)
        if m:
            start, end = m.span()
            span = _norm(text[max(0, start - 220): min(len(text), end + 220)])
            try:
                val = float(m.group(1))
            except (ValueError, IndexError):
                continue
            family = None
            for fam, aliases in FAMILY_ALIASES.items():
                if any(a in low for a in aliases):
                    family = fam
                    break
            found.append(("PARAMETER_DECLARATION", family, [], False, span, start, end,
                          {"param_name": pname, "param_value": val}))
    return found


def run_extraction(conn, import_run_id: str) -> dict:
    """Extract evidence from all pages of this import run's document versions."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT dv.document_version_id, dv.work_id, dv.fulltext_sha256,
                   p.page_id, p.physical_page, p.text_content, p.document_node_id
            FROM epidemic_kg.kg_document_versions dv
            JOIN epidemic_kg.kg_document_pages p
              ON p.document_node_id IN (
                 SELECT node_id FROM epidemic_kg.kg_nodes
                 WHERE node_type='DOCUMENT_VERSION'
                   AND properties->>'pdf_sha256' = dv.fulltext_sha256)
            WHERE dv.import_run_id = %s AND p.text_content IS NOT NULL
            ORDER BY dv.fulltext_sha256, p.physical_page
        """, (import_run_id,))
        pages = cur.fetchall()

    stats = {"pages_scanned": 0, "spans_created": 0, "assertions_created": 0,
             "param_assertions": 0, "works_with_evidence": set(),
             "family_work_counts": {}}
    with conn.cursor() as cur:
        for dv_id, work_id, sha, page_id, ppage, text, doc_node_id in pages:
            stats["pages_scanned"] += 1
            hits = extract_from_page(text or "")
            fam_count = {}
            for kind, family, diseases, generic, span, start, end, extra in hits:
                if not family:
                    family = "Poisson event-rate (Gamma prior)" if kind == "PARAMETER_DECLARATION" else None
                if not family:
                    continue
                key = (family, str(dv_id))
                fam_count[key] = fam_count.get(key, 0) + 1
                if fam_count[key] > MAX_SPANS_PER_FAMILY_WORK and kind != "PARAMETER_DECLARATION":
                    continue
                span_id = stable_uuid("span", sha, str(ppage), str(start), str(end),
                                      sha256_text(span))
                cur.execute("""
                    INSERT INTO epidemic_kg.kg_evidence_spans
                    (evidence_span_id, document_node_id, physical_page, printed_page_label,
                     section_label, table_or_figure, raw_anchor, anchor_start, anchor_end,
                     page_text_sha256, anchor_sha256, source_pdf_sha256,
                     evidence_eligible, quarantine, properties)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (document_node_id, physical_page, anchor_start, anchor_end, anchor_sha256)
                    DO NOTHING
                    RETURNING evidence_span_id
                """, (span_id, str(doc_node_id), ppage, str(ppage), "", "",
                      span, start, end, sha256_text(text or ""), sha256_text(span), sha,
                      True, False,
                      json.dumps({"extractor": EXTRACTOR, "extractor_version": EXTRACTOR_VERSION,
                                  "review_level": "MACHINE_EXTRACTED",
                                  "document_version_id": str(dv_id)}, ensure_ascii=False)))
                row = cur.fetchone()
                if row:
                    stats["spans_created"] += 1
                else:
                    # identical span already exists (possibly for another family) -> reuse it
                    cur.execute("""
                        SELECT evidence_span_id FROM epidemic_kg.kg_evidence_spans
                        WHERE document_node_id=%s AND physical_page=%s AND anchor_start=%s
                          AND anchor_end=%s AND anchor_sha256=%s
                    """, (str(doc_node_id), ppage, start, end, sha256_text(span)))
                    span_id = str(cur.fetchone()[0])

                if kind == "MODEL_MENTION":
                    # every span gets a provenance assertion (no orphans)
                    aid = _insert_assertion(
                        cur, import_run_id, "MODEL_FAMILY", family,
                        "MENTIONED_IN_WORK", "WORK", str(work_id) if work_id else "ORPHAN_PDF",
                        span_id, work_id)
                    if aid:
                        stats["assertions_created"] += 1
                    if diseases:
                        for d in diseases:
                            aid = _insert_assertion(
                                cur, import_run_id, "MODEL_FAMILY", family,
                                "APPLIED_TO_DISEASE", "DISEASE", d, span_id, work_id)
                            if aid:
                                stats["assertions_created"] += 1
                    if generic or diseases:
                        aid = _insert_assertion(
                            cur, import_run_id, "MODEL_FAMILY", family,
                            "INFECTIOUS_DISEASE_EVIDENCE", "GENERIC", "infectious_disease",
                            span_id, work_id)
                        if aid:
                            stats["assertions_created"] += 1
                elif kind == "PARAMETER_DECLARATION":
                    aid = _insert_assertion(
                        cur, import_run_id, "MODEL_FAMILY", family,
                        "DECLARES_PARAMETER", "PARAMETER", extra["param_name"],
                        span_id, work_id, value=str(extra["param_value"]))
                    if aid:
                        stats["assertions_created"] += 1
                        stats["param_assertions"] += 1
                if work_id:
                    stats["works_with_evidence"].add(str(work_id))
    stats["works_with_evidence"] = len(stats["works_with_evidence"])
    stats["family_work_counts"] = len(stats["family_work_counts"])
    return stats


def _insert_assertion(cur, import_run_id, skind, sid, pred, okind, oid, span_id, work_id,
                      value=None):
    natural = f"{skind}|{sid}|{pred}|{oid}|{value or ''}|{work_id or ''}"
    aid = stable_uuid("assertion", natural)
    cur.execute("""
        INSERT INTO epidemic_kg.kg_assertions
        (assertion_id, subject_kind, subject_id, predicate, object_kind, object_id,
         value_text, polarity, scope, confidence, review_status,
         extractor, extractor_version, import_run_id, natural_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,'SUPPORTS',%s::jsonb,%s,'MACHINE_EXTRACTED',%s,%s,%s,%s)
        ON CONFLICT (natural_key, import_run_id) DO NOTHING
        RETURNING assertion_id
    """, (aid, skind, sid, pred, okind, oid, value,
          json.dumps({"work_id": str(work_id) if work_id else None}),
          0.6, EXTRACTOR, EXTRACTOR_VERSION, import_run_id, natural))
    row = cur.fetchone()
    if not row:
        # assertion exists already -> bind THIS span to it as well
        cur.execute("""SELECT assertion_id FROM epidemic_kg.kg_assertions
                       WHERE natural_key=%s AND import_run_id=%s""", (natural, import_run_id))
        aid = str(cur.fetchone()[0])
        cur.execute("""
            INSERT INTO epidemic_kg.kg_assertion_evidence (assertion_id, evidence_span_id)
            VALUES (%s,%s) ON CONFLICT DO NOTHING
        """, (aid, span_id))
        return None
    cur.execute("""
        INSERT INTO epidemic_kg.kg_assertion_evidence (assertion_id, evidence_span_id)
        VALUES (%s,%s) ON CONFLICT DO NOTHING
    """, (aid, span_id))
    return aid


def build_evidence_index(conn, import_run_id: str) -> dict:
    """{model_family: {same_disease: [(work_id, span_id, disease)], generic: [...], param: [...]}}
    Only assertions bound to page-level spans enter the index."""
    idx: dict = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.subject_id, a.predicate, a.object_id, a.scope->>'work_id',
                   ae.evidence_span_id
            FROM epidemic_kg.kg_assertions a
            JOIN epidemic_kg.kg_assertion_evidence ae ON ae.assertion_id = a.assertion_id
            JOIN epidemic_kg.kg_evidence_spans s ON s.evidence_span_id = ae.evidence_span_id
            WHERE a.import_run_id = %s AND s.physical_page IS NOT NULL
              AND s.evidence_eligible AND NOT s.quarantine
        """, (import_run_id,))
        for family, pred, oid, work_id, span_id in cur.fetchall():
            e = idx.setdefault(family, {"same_disease": [], "generic": [], "param": []})
            if pred == "APPLIED_TO_DISEASE":
                e["same_disease"].append((work_id, str(span_id), oid))
            elif pred == "INFECTIOUS_DISEASE_EVIDENCE":
                e["generic"].append((work_id, str(span_id)))
            elif pred == "DECLARES_PARAMETER":
                e["param"].append((work_id, str(span_id)))
    return idx
