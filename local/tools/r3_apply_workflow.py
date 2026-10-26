#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import uuid
import zipfile
from io import BytesIO
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import psycopg  # type: ignore
from pypdf import PdfReader  # type: ignore

TASK_ID = "EPIDEMIC_KG_PREDICTION_R3_104_GATE_FULLTEXT_MODEL_BRIDGE"
NS = uuid.UUID("64de5cf9-e4d0-5f7e-8d0b-e1d419074004")
MODEL_RE = re.compile(r"\b(XGBoost|LightGBM|random forest|LSTM|GRU|RNN|Transformer|ARIMA|SARIMA|SEIR|SIR|agent[- ]based|Bayesian|Gaussian process|neural network|graph neural|GNN|regression|state[- ]space|Kalman|naive|baseline)\b", re.I)
PARAM_RE = re.compile(r"\b(horizon|lag|window|learning rate|depth|estimators|hidden|layers|season|period|R0|beta|gamma|incubation|serial interval)\b.{0,80}", re.I)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{key}")


def sha(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8", "ignore")
    return hashlib.sha256(s).hexdigest()


def conn(dbname: str):
    return psycopg.connect(host=str(PROJECT / "runtime" / "postgres-run"), port=55432, user="postgres", dbname=dbname)


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ensure_schema(cur, execute: bool) -> None:
    ddl = """
    create table if not exists epidemic_kg.kg_document_pages (
      page_id uuid primary key,
      document_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
      physical_page integer not null check (physical_page >= 1),
      printed_page_label text not null default '',
      extraction_tool text not null,
      extraction_status text not null,
      text_content text not null,
      text_sha256 text not null check (text_sha256 ~ '^[0-9a-f]{64}$'),
      char_count integer not null check (char_count >= 0),
      created_at timestamptz not null default now(),
      unique(document_node_id, physical_page)
    );
    create table if not exists epidemic_kg.kg_document_chunks (
      chunk_id uuid primary key,
      page_id uuid not null references epidemic_kg.kg_document_pages(page_id),
      document_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
      physical_page integer not null check (physical_page >= 1),
      chunk_index integer not null check (chunk_index >= 0),
      chunk_text text not null,
      chunk_sha256 text not null check (chunk_sha256 ~ '^[0-9a-f]{64}$'),
      token_estimate integer not null check (token_estimate >= 0),
      created_at timestamptz not null default now(),
      unique(document_node_id, physical_page, chunk_index)
    );
    create index if not exists kg_document_chunks_text_tsv_idx
      on epidemic_kg.kg_document_chunks using gin (to_tsvector('english', chunk_text));
    create table if not exists epidemic_kg.kg_pending_claim_candidates (
      candidate_id uuid primary key,
      document_node_id uuid not null references epidemic_kg.kg_nodes(node_id),
      work_node_id uuid references epidemic_kg.kg_nodes(node_id),
      page_id uuid references epidemic_kg.kg_document_pages(page_id),
      chunk_id uuid references epidemic_kg.kg_document_chunks(chunk_id),
      claim_type text not null,
      disease_text text,
      model_text text,
      parameter_text text,
      evidence_anchor text not null,
      evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
      status text not null default 'MACHINE_EXTRACTED_PENDING_REVIEW',
      recommendation_eligible boolean not null default false,
      properties jsonb not null default '{}'::jsonb,
      created_at timestamptz not null default now(),
      unique(document_node_id, claim_type, evidence_sha256)
    );
    """
    if execute:
        cur.execute(ddl)


def counts(cur) -> dict[str, int]:
    out = {}
    for name, sql in {
        "work": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK'",
        "document_version": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
        "claim": "select count(*) from epidemic_kg.kg_claims",
        "eligible_claim": "select count(*) from epidemic_kg.kg_claims where evidence_eligible and not quarantine",
        "evidence_span": "select count(*) from epidemic_kg.kg_evidence_spans",
        "edge": "select count(*) from epidemic_kg.kg_edges",
    }.items():
        cur.execute(sql); out[name] = cur.fetchone()[0]
    for name, table in {"page": "kg_document_pages", "chunk": "kg_document_chunks", "pending_claim_candidate": "kg_pending_claim_candidates"}.items():
        cur.execute("select to_regclass(%s)", (f"epidemic_kg.{table}",))
        if cur.fetchone()[0]:
            cur.execute(f"select count(*) from epidemic_kg.{table}")
            out[name] = cur.fetchone()[0]
        else:
            out[name] = 0
    return out


def title_key(title: str, legacy: str, h: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")[:80]
    return f"work:r3:title-year:{legacy}:{base}:{h[:12]}"


def get_doc(cur, h: str) -> dict | None:
    cur.execute("select node_id, canonical_key, display_name, properties from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION' and properties->>'pdf_sha256'=%s limit 1", (h,))
    r = cur.fetchone()
    if not r:
        return None
    return {"node_id": r[0], "canonical_key": r[1], "display_name": r[2], "properties": r[3] or {}}


def get_work_for_doc(cur, doc_id) -> dict | None:
    cur.execute("""
      select w.node_id, w.canonical_key, w.display_name, w.properties
      from epidemic_kg.kg_edges e join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id
      where e.subject_node_id=%s and e.predicate='IS_VERSION_OF' limit 1
    """, (doc_id,))
    r = cur.fetchone()
    return {"node_id": r[0], "canonical_key": r[1], "display_name": r[2], "properties": r[3] or {}} if r else None


def suspicious_work(work: dict, grouped_titles: dict[str, set[str]]) -> bool:
    key = work["canonical_key"].lower()
    doi = str((work.get("properties") or {}).get("normalized_doi") or "").lower()
    if re.fullmatch(r"work:doi:10\.\d{4,9}/[a-z]?", key) or key.endswith(("10.1016/j", "10.1073/pnas")):
        return True
    if len(grouped_titles.get(key, set())) > 1 and (doi.startswith("10.") or key.startswith("work:doi:10.")):
        return True
    return False


def read_pdf_texts(zip_path: Path, member: str, expected_sha256: str | None = None) -> tuple[list[str], str]:
    member_name = member.replace("PDF.zip/", "PDF/")
    with zipfile.ZipFile(zip_path) as z:
        try:
            data = z.read(member_name)
            resolved_member = member_name
        except KeyError:
            if not expected_sha256:
                raise
            resolved_member = ""
            data = b""
            for candidate in z.namelist():
                if candidate.endswith("/"):
                    continue
                candidate_data = z.read(candidate)
                if sha(candidate_data) == expected_sha256:
                    resolved_member = candidate
                    data = candidate_data
                    break
            if not resolved_member:
                raise
        reader = PdfReader(BytesIO(data))
        texts = []
        for page in reader.pages:
            try:
                texts.append((page.extract_text() or "").replace("\x00", ""))
            except Exception:
                texts.append("")
        return texts, resolved_member


def chunks(text: str, size: int = 1800, overlap: int = 180) -> list[str]:
    text = (text or "").replace("\x00", "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return [""]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i+size])
        i += max(1, size-overlap)
    return out


def extract_claims(chunk_text: str) -> list[dict]:
    found = []
    m = MODEL_RE.search(chunk_text or "")
    if not m:
        return found
    anchor = chunk_text[max(0, m.start()-220): min(len(chunk_text), m.end()+320)]
    p = PARAM_RE.search(anchor)
    found.append({
        "claim_type": "MENTIONS_MODEL_OR_PARAMETER",
        "model_text": m.group(0),
        "parameter_text": p.group(0) if p else None,
        "evidence_anchor": anchor[:900],
    })
    return found


def run_workflow(dbname: str, manifest: Path, zip_path: Path, report: Path, execute: bool) -> int:
    rows = read_manifest(manifest)
    result = {
        "schema_version": "1.0", "task_id": TASK_ID, "execution_id": report.parent.name, "generated_at_utc": now(),
        "active_project_root": str(PROJECT), "source_revision_or_hash": "r3_apply_workflow:" + sha(Path(__file__).read_bytes()),
        "database_identity": {"database": dbname, "port": 55432, "user": "postgres"},
        "input_manifest_sha256": sha(manifest.read_bytes()), "result_status": "STARTED", "commands_or_evidence_refs": [],
        "blockers": [], "no_fake_pass_assertion": True, "execute": execute,
    }
    per_doc = []
    with conn(dbname) as c, c.cursor() as cur:
        before = counts(cur)
        ensure_schema(cur, execute)
        grouped_titles = defaultdict(set)
        docs = {}
        works = {}
        for row in rows:
            doc = get_doc(cur, row["source_sha256"])
            if doc:
                work = get_work_for_doc(cur, doc["node_id"])
                docs[row["source_sha256"]] = doc
                works[row["source_sha256"]] = work
                if work:
                    grouped_titles[work["canonical_key"].lower()].add(re.sub(r"[^a-z0-9]+", " ", doc["display_name"].lower()).strip())

        changed = Counter()
        for row in rows:
            h = row["source_sha256"]
            doc = docs.get(h)
            if not doc:
                per_doc.append({"target_item_id": row["target_item_id"], "status": "BLOCKED_DOCUMENT_NOT_IN_LIVE_KG"})
                continue
            work = works.get(h)
            work_id = work["node_id"] if work else None
            repair_action = "NO_IDENTITY_REPAIR"
            if work and suspicious_work(work, grouped_titles):
                new_key = title_key(row["canonical_title"], row["legacy_paper_number"], h)
                new_work_id = sid("r3-repaired-work", new_key)
                repair_action = "REPAIR_TO_TITLE_YEAR_WORK"
                if execute:
                    cur.execute("""
                    insert into epidemic_kg.kg_nodes(node_id,canonical_key,node_type,display_name,status,trust_status,review_status,quarantine,evidence_eligible,properties)
                    values(%s,%s,'WORK',%s,'ACTIVE','REGISTERED','PENDING_REVIEW',false,false,%s::jsonb)
                    on conflict(canonical_key) do update set display_name=excluded.display_name, properties=epidemic_kg.kg_nodes.properties || excluded.properties, updated_at=now()
                    """, (new_work_id, new_key, row["canonical_title"][:500], json.dumps({
                        "source":"EPIDEMIC_KG_PREDICTION_R3", "identity_repair":"R2_TRUNCATED_DOI_OR_BAD_MERGE_REPAIR",
                        "legacy_paper_number": row["legacy_paper_number"], "supersedes_suspicious_work_key": work["canonical_key"],
                        "pdf_sha256": h, "canonical_title": row["canonical_title"], "recommendation_eligible": False,
                    }, ensure_ascii=False)))
                    if cur.rowcount: changed["identity_repaired_work_upsert"] += 1
                    cur.execute("update epidemic_kg.kg_edges set object_node_id=%s, properties=properties || %s::jsonb where subject_node_id=%s and predicate='IS_VERSION_OF'",
                                (new_work_id, json.dumps({"identity_repair":"EPIDEMIC_KG_PREDICTION_R3"}, ensure_ascii=False), doc["node_id"]))
                    changed["identity_edge_relinked"] += cur.rowcount
                work_id = new_work_id

            page_count = 0; chunk_count = 0; cand_count = 0
            resolved_member = row["source_relative_ref"]
            try:
                texts, resolved_member = read_pdf_texts(zip_path, row["source_relative_ref"], h)
            except Exception as e:
                per_doc.append({"target_item_id": row["target_item_id"], "status": "TEXT_EXTRACTION_FAILED", "error": type(e).__name__, "identity_repair": repair_action})
                continue
            for pnum, text in enumerate(texts, start=1):
                page_id = sid("page", f"{h}:{pnum}")
                page_sha = sha(text)
                if execute:
                    cur.execute("""
                    insert into epidemic_kg.kg_document_pages(page_id,document_node_id,physical_page,printed_page_label,extraction_tool,extraction_status,text_content,text_sha256,char_count)
                    values(%s,%s,%s,%s,'pypdf','TEXT_EXTRACTED',%s,%s,%s)
                    on conflict(document_node_id,physical_page) do nothing
                    """, (page_id, doc["node_id"], pnum, str(pnum), text, page_sha, len(text)))
                    if cur.rowcount: changed["page"] += 1
                page_count += 1
                for idx, ch in enumerate(chunks(text)):
                    if not ch:
                        continue
                    chunk_id = sid("chunk", f"{h}:{pnum}:{idx}:{sha(ch)}")
                    if execute:
                        cur.execute("""
                        insert into epidemic_kg.kg_document_chunks(chunk_id,page_id,document_node_id,physical_page,chunk_index,chunk_text,chunk_sha256,token_estimate)
                        values(%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict(document_node_id,physical_page,chunk_index) do nothing
                        """, (chunk_id, page_id, doc["node_id"], pnum, idx, ch, sha(ch), max(1, len(ch)//4)))
                        if cur.rowcount: changed["chunk"] += 1
                    chunk_count += 1
                    for claim in extract_claims(ch):
                        cand_id = sid("pending-claim", f"{h}:{pnum}:{idx}:{claim['claim_type']}:{sha(claim['evidence_anchor'])}")
                        if execute:
                            cur.execute("""
                            insert into epidemic_kg.kg_pending_claim_candidates(candidate_id,document_node_id,work_node_id,page_id,chunk_id,claim_type,model_text,parameter_text,evidence_anchor,evidence_sha256,status,recommendation_eligible,properties)
                            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MACHINE_EXTRACTED_PENDING_REVIEW',false,%s::jsonb)
                            on conflict(document_node_id,claim_type,evidence_sha256) do nothing
                            """, (cand_id, doc["node_id"], work_id, page_id, chunk_id, claim["claim_type"], claim["model_text"], claim["parameter_text"], claim["evidence_anchor"], sha(claim["evidence_anchor"]), json.dumps({"legacy_paper_number": row["legacy_paper_number"], "source":"EPIDEMIC_KG_PREDICTION_R3"}, ensure_ascii=False)))
                            if cur.rowcount: changed["pending_claim_candidate"] += 1
                        cand_count += 1
            per_doc.append({"target_item_id": row["target_item_id"], "legacy_paper_number": row["legacy_paper_number"], "status": "TEXT_EXTRACTED_AND_CHUNKED", "identity_repair": repair_action, "resolved_zip_member": resolved_member, "page_count": page_count, "chunk_count": chunk_count, "pending_candidate_count": cand_count})
        after = counts(cur) if execute else before.copy()
        if execute:
            c.commit()
        else:
            c.rollback()
    result.update({"result_status": "COMPLETE" if execute else "DRY_RUN_COMPLETE", "before_counts": before, "after_counts": after, "delta_counts": {k: after.get(k,0)-before.get(k,0) for k in sorted(set(before)|set(after))}, "changed": dict(changed), "per_document": per_doc})
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["result_status"], "delta": result["delta_counts"], "changed": result["changed"]}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbname", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--pdf-zip", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--execute", action="store_true")
    ns = ap.parse_args()
    return run_workflow(ns.dbname, Path(ns.manifest), Path(ns.pdf_zip), Path(ns.report), ns.execute)


if __name__ == "__main__":
    raise SystemExit(main())
