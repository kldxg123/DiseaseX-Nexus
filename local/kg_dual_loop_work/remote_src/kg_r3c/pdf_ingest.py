"""kg_r3c PDF ingest: stream nested-zip PDFs, extract text, populate KG tables.

PARSER_VERSION = 'kg_r3c.pdf_ingest.v1'

Reads PDF members listed in 06_ARCHIVE_MEMBER_MANIFEST.csv from the 13 nested
zips under the staging dir, verifies each member's sha256 against the
pre-computed manifest value, extracts per-page text with pypdf, and writes:

- epidemic_kg.kg_document_versions (one row per new unique fulltext sha256)
- epidemic_kg.kg_document_pages    (one row per page, stable page_id)
- epidemic_kg.kg_nodes             (DOCUMENT_VERSION node per unique PDF;
                                    WORK node when a matched work has none)
- epidemic_kg.kg_edges             (IS_VERSION_OF DOCUMENT_VERSION -> WORK)
- epidemic_kg.kg_review_items      (PDF_HASH_MISMATCH / PDF_PARSE_FAILED /
                                    PDF_DUPLICATE_ALIAS / OCR_REQUIRED /
                                    ORPHAN_PDF / ORPHAN_METADATA)

Idempotent: all ids are stable uuid5 values; inserts use ON CONFLICT DO
NOTHING; duplicates are detected by fulltext sha256 against both
kg_document_versions and existing DOCUMENT_VERSION kg_nodes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field

import pypdf
from psycopg.types.json import Json

from epidemic_core.kg_r3c.dbutil import connect, sha256_text, stable_uuid

PARSER_NAME = "pypdf"
PARSER_VERSION = "kg_r3c.pdf_ingest.v1"
IMPORT_RUN_ID = "bef74d0c-f60e-58d8-af98-d646a6401958"
DOC_NODE_SOURCE = "kg_r3c"

LOG = logging.getLogger("kg_r3c.pdf_ingest")

_CHUNK = 1024 * 1024
_BATCH_RE = re.compile(r"^第(\d+)")
_NUMPREFIX_RE = re.compile(r"^\d+_")
_CJK_RE = re.compile(r"[一-鿿]")


@dataclass
class WorkRef:
    work_id: str
    title: str
    doi: str
    zotero_key: str
    source_member: str


@dataclass
class Accounting:
    pdf_member_count: int = 0
    attempted: int = 0
    unique_fulltext_sha256_count: int = 0
    new_document_version_count: int = 0
    duplicate_exact_count: int = 0
    skipped_already_ingested: int = 0
    fulltext_linked_count: int = 0
    orphan_pdf_count: int = 0
    no_text_layer_count: int = 0
    ocr_required_count: int = 0
    failed_parse_count: int = 0
    quarantined_count: int = 0
    hash_mismatch_count: int = 0
    total_pages_extracted: int = 0
    pages_with_text: int = 0
    pages_no_text: int = 0
    orphan_metadata_count: int = 0
    per_zip: dict = field(default_factory=dict)

    def zip_bucket(self, member_path: str) -> dict:
        return self.per_zip.setdefault(member_path, {
            "pdf_members": 0, "attempted": 0, "new_document_versions": 0,
            "duplicates": 0, "skipped_already_ingested": 0, "failed_parse": 0, "hash_mismatch": 0,
            "linked": 0, "orphan_pdf": 0, "ocr_required": 0,
            "pages": 0, "pages_with_text": 0, "pages_no_text": 0,
        })


def batch_of(member_path: str) -> str | None:
    m = _BATCH_RE.match(member_path)
    return m.group(1) if m else None


def strip_num_prefix(basename: str) -> str:
    return _NUMPREFIX_RE.sub("", basename)


def sanitize_text(text: str) -> str:
    """PostgreSQL text fields reject NUL bytes; strip them."""
    return text.replace("\x00", "")


def detect_language(text: str) -> str:
    sample = text[:2000]
    if not sample:
        return "en"
    cjk = len(_CJK_RE.findall(sample))
    return "zh" if cjk / max(len(sample), 1) > 0.20 else "en"


def load_manifest_pdfs(manifest_csv: str) -> list[dict]:
    rows = []
    with open(manifest_csv, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["kind"] != "pdf":
                continue
            if r["quarantined"].strip().lower() in ("1", "true", "yes"):
                continue
            rows.append({
                "member_path": r["member_path"],
                "inner_path": r["inner_path"],
                "member_sha256": r["member_sha256"].strip().lower(),
                "size": int(r["size"] or 0),
            })
    return rows


def build_attachment_map(conn) -> dict:
    """(batch, attachment basename lower) -> list[WorkRef]."""
    cur = conn.cursor()
    cur.execute(
        "select work_id, title, coalesce(raw_metadata->>'DOI',''), zotero_key, "
        "metadata_source_member, coalesce(raw_metadata->>'File Attachments','') "
        "from epidemic_kg.kg_paper_works"
    )
    amap: dict = {}
    for work_id, title, doi, zkey, src_member, attachments in cur.fetchall():
        b = batch_of(src_member or "")
        if b is None:
            continue
        ref = WorkRef(str(work_id), title or "", (doi or "").strip(), zkey or "", src_member)
        for part in attachments.split(";"):
            part = part.strip()
            if not part:
                continue
            base = part.replace("\\", "/").rsplit("/", 1)[-1].strip()
            if not base.lower().endswith(".pdf"):
                continue
            amap.setdefault((b, base.lower()), []).append(ref)
    return amap


def match_work(amap: dict, member_path: str, inner_path: str) -> WorkRef | None:
    b = batch_of(member_path)
    if b is None:
        return None
    base = inner_path.replace("\\", "/").rsplit("/", 1)[-1]
    cands = [base.lower()]
    stripped = strip_num_prefix(base).lower()
    if stripped != cands[0]:
        cands.append(stripped)
    for c in cands:
        hits = amap.get((b, c))
        if hits:
            return hits[0]
    return None


class PdfIngestor:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self.acc = Accounting()
        # sha256 -> (member_path, inner_path) first-seen source, from prior dv rows
        self.dv_sources: dict[str, tuple[str, str]] = {}
        self.legacy_node_shas: set[str] = set()
        self.work_node_by_key: dict[str, str] = {}
        self.linked_work_ids: set[str] = set()

    def load_existing(self):
        self.cur.execute(
            "select fulltext_sha256, source_member_path, source_inner_path "
            "from epidemic_kg.kg_document_versions"
        )
        for sha, mp, ip in self.cur.fetchall():
            self.dv_sources[sha] = (mp or "", ip or "")
        self.cur.execute(
            "select substring(canonical_key from 14) from epidemic_kg.kg_nodes "
            "where node_type='DOCUMENT_VERSION' and canonical_key like 'paper:sha256:%'"
        )
        self.legacy_node_shas = {r[0] for r in self.cur.fetchall()} - set(self.dv_sources)
        self.cur.execute(
            "select canonical_key, node_id from epidemic_kg.kg_nodes where node_type='WORK'"
        )
        for ck, nid in self.cur.fetchall():
            self.work_node_by_key[ck] = str(nid)
        LOG.info("existing: %d dv rows, %d legacy doc nodes, %d work nodes",
                 len(self.dv_sources), len(self.legacy_node_shas), len(self.work_node_by_key))

    # ---------- review items ----------

    def add_review(self, item_kind: str, target_id: str, reason: str, payload: dict):
        rid = stable_uuid("review", IMPORT_RUN_ID, item_kind, target_id, reason)
        self.cur.execute(
            "insert into epidemic_kg.kg_review_items "
            "(review_item_id, item_kind, target_id, reason, status, payload, import_run_id, created_at) "
            "values (%s,%s,%s,%s,'OPEN',%s,%s,now()) on conflict do nothing",
            (rid, item_kind, target_id, reason, Json(payload), IMPORT_RUN_ID),
        )

    # ---------- nodes / edges ----------

    def doc_node_id(self, sha: str) -> str:
        return stable_uuid("docver", sha)

    def resolve_doc_node_id(self, sha: str) -> str | None:
        """Actual node_id for canonical key paper:sha256:<sha> (may differ from
        stable_uuid for pre-existing legacy nodes)."""
        self.cur.execute(
            "select node_id from epidemic_kg.kg_nodes "
            "where node_type='DOCUMENT_VERSION' and canonical_key=%s",
            (f"paper:sha256:{sha}",),
        )
        row = self.cur.fetchone()
        return str(row[0]) if row else None

    def ensure_doc_node(self, sha: str, page_count: int, size: int,
                        member_path: str, inner_path: str, text_layer_status: str):
        nid = self.doc_node_id(sha)
        display = inner_path.replace("\\", "/").rsplit("/", 1)[-1]
        props = {
            "source": DOC_NODE_SOURCE,
            "media_type": "application/pdf",
            "pdf_sha256": sha,
            "page_count": str(page_count),
            "size_bytes": str(size),
            "text_status": text_layer_status,
            "source_member_path": member_path,
            "source_inner_path": inner_path,
            "import_run_id": IMPORT_RUN_ID,
            "parser_name": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "recommendation_eligible": False,
        }
        self.cur.execute(
            "insert into epidemic_kg.kg_nodes "
            "(node_id, canonical_key, node_type, display_name, status, trust_status, "
            " review_status, quarantine, evidence_eligible, properties, created_at, updated_at) "
            "values (%s,%s,'DOCUMENT_VERSION',%s,'ACTIVE','REGISTERED','PENDING_REVIEW',"
            "        false,false,%s,now(),now()) on conflict do nothing",
            (nid, f"paper:sha256:{sha}", display, Json(props)),
        )
        # Insert may have been skipped on canonical_key conflict with a
        # pre-existing node whose node_id differs from stable_uuid; always
        # return the node_id actually present in kg_nodes.
        return self.resolve_doc_node_id(sha) or nid

    def ensure_work_node(self, work: WorkRef) -> str:
        if work.doi:
            ckey = f"work:doi:{work.doi.lower()}"
        else:
            ckey = f"work:zotero:{work.zotero_key}"
        nid = self.work_node_by_key.get(ckey)
        if nid:
            return nid
        nid = stable_uuid("worknode", work.work_id)
        props = {
            "source": DOC_NODE_SOURCE,
            "work_id": work.work_id,
            "zotero_key": work.zotero_key,
            "normalized_doi": work.doi.lower() if work.doi else "",
            "canonical_title": work.title,
            "metadata_source_member": work.source_member,
            "import_run_id": IMPORT_RUN_ID,
            "recommendation_eligible": False,
        }
        self.cur.execute(
            "insert into epidemic_kg.kg_nodes "
            "(node_id, canonical_key, node_type, display_name, status, trust_status, "
            " review_status, quarantine, evidence_eligible, properties, created_at, updated_at) "
            "values (%s,%s,'WORK',%s,'ACTIVE','REGISTERED','PENDING_REVIEW',"
            "        false,false,%s,now(),now()) on conflict do nothing",
            (nid, ckey, work.title, Json(props)),
        )
        # Re-resolve in case the insert conflicted with an existing node
        # carrying the same canonical_key but a different node_id.
        self.cur.execute(
            "select node_id from epidemic_kg.kg_nodes where canonical_key=%s", (ckey,)
        )
        row = self.cur.fetchone()
        nid = str(row[0]) if row else nid
        self.work_node_by_key[ckey] = nid
        return nid

    def ensure_is_version_of_edge(self, doc_nid: str, work_nid: str):
        eid = stable_uuid("edge", doc_nid, "IS_VERSION_OF", work_nid)
        props = {
            "source": DOC_NODE_SOURCE,
            "relation": "DOCUMENT_VERSION_VERSION_OF_WORK",
            "import_run_id": IMPORT_RUN_ID,
        }
        self.cur.execute(
            "insert into epidemic_kg.kg_edges "
            "(edge_id, subject_node_id, predicate, object_node_id, relation_status, "
            " evidence_eligible, properties, created_at) "
            "values (%s,%s,'IS_VERSION_OF',%s,'SYSTEM_DERIVED',false,%s,now()) "
            "on conflict do nothing",
            (eid, doc_nid, work_nid, Json(props)),
        )

    # ---------- per-PDF processing ----------

    def read_member(self, zf: zipfile.ZipFile, inner_path: str) -> tuple[bytes, str]:
        h = hashlib.sha256()
        buf = bytearray()
        with zf.open(inner_path) as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                buf.extend(chunk)
        return bytes(buf), h.hexdigest()

    def link_work(self, sha: str, work: WorkRef | None, member_path: str,
                  inner_path: str, doc_nid: str | None, update_dv: bool) -> bool:
        """Link a document version to a work. Returns True if linked."""
        if work is None:
            return False
        work_nid = self.ensure_work_node(work)
        if doc_nid is None:
            doc_nid = self.resolve_doc_node_id(sha) or self.doc_node_id(sha)
        self.ensure_is_version_of_edge(doc_nid, work_nid)
        if update_dv:
            self.cur.execute(
                "update epidemic_kg.kg_document_versions set work_id=%s "
                "where fulltext_sha256=%s and work_id is null",
                (work.work_id, sha),
            )
        self.linked_work_ids.add(work.work_id)
        return True

    def process_pdf(self, zf: zipfile.ZipFile, row: dict, amap: dict):
        acc, zb = self.acc, self.acc.zip_bucket(row["member_path"])
        mp, ip, expected = row["member_path"], row["inner_path"], row["member_sha256"]
        acc.attempted += 1
        zb["attempted"] += 1
        try:
            data, sha = self.read_member(zf, ip)
        except Exception as e:  # zip read failure
            acc.hash_mismatch_count += 1
            zb["hash_mismatch"] += 1
            self.add_review("PDF_HASH_MISMATCH", stable_uuid("pdftarget", mp, ip),
                            f"unreadable zip member {mp}:{ip}",
                            {"member_path": mp, "inner_path": ip,
                             "error_type": type(e).__name__, "stage": "zip_read"})
            return
        if sha != expected:
            acc.hash_mismatch_count += 1
            zb["hash_mismatch"] += 1
            acc.quarantined_count += 1
            self.add_review("PDF_HASH_MISMATCH", stable_uuid("pdftarget", mp, ip),
                            f"sha256 mismatch {mp}:{ip}",
                            {"member_path": mp, "inner_path": ip,
                             "expected_sha256": expected, "actual_sha256": sha})
            return

        work = match_work(amap, mp, ip)

        # --- dedup by fulltext sha256 ---
        if sha in self.dv_sources or sha in self.legacy_node_shas:
            if self.dv_sources.get(sha) == (mp, ip):
                # Same source path already ingested by a previous (partial)
                # run of this importer: idempotent no-op, not a true duplicate.
                acc.skipped_already_ingested += 1
                zb["skipped_already_ingested"] += 1
                return
            acc.duplicate_exact_count += 1
            zb["duplicates"] += 1
            if sha in self.dv_sources:
                first_mp, first_ip = self.dv_sources[sha]
                reason = f"alias of {first_mp}:{first_ip}"
            else:
                first_mp, first_ip = "", ""
                reason = "alias of pre-existing DOCUMENT_VERSION node"
            self.add_review("PDF_DUPLICATE_ALIAS", stable_uuid("pdftarget", mp, ip), reason,
                            {"member_path": mp, "inner_path": ip,
                             "first_member_path": first_mp, "first_inner_path": first_ip,
                             "fulltext_sha256": sha})
            if self.link_work(sha, work, mp, ip, None, update_dv=sha in self.dv_sources):
                acc.fulltext_linked_count += 1
                zb["linked"] += 1
            return

        # --- new unique PDF ---
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            page_count = len(reader.pages)
            texts = []
            for pg in reader.pages:
                try:
                    texts.append(sanitize_text(pg.extract_text() or ""))
                except Exception:
                    texts.append("")
        except Exception as e:
            acc.failed_parse_count += 1
            zb["failed_parse"] += 1
            self.add_review("PDF_PARSE_FAILED", stable_uuid("pdftarget", mp, ip),
                            f"pypdf parse failed {mp}:{ip}",
                            {"member_path": mp, "inner_path": ip,
                             "error_type": type(e).__name__, "error": str(e)[:500]})
            return

        acc.unique_fulltext_sha256_count += 1
        acc.new_document_version_count += 1
        zb["new_document_versions"] += 1
        any_text = any(t.strip() for t in texts)
        text_layer_status = "TEXT_EXTRACTED" if any_text else "NO_TEXT_LAYER"
        doc_nid = self.ensure_doc_node(sha, page_count, row["size"], mp, ip, text_layer_status)

        extraction_tool = f"pypdf {pypdf.__version__}"
        for i, text in enumerate(texts, start=1):
            has_text = bool(text.strip())
            self.cur.execute(
                "insert into epidemic_kg.kg_document_pages "
                "(page_id, document_node_id, physical_page, printed_page_label, "
                " extraction_tool, extraction_status, text_content, text_sha256, "
                " char_count, created_at) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) on conflict do nothing",
                (stable_uuid("page", sha, str(i)), doc_nid, i, str(i),
                 extraction_tool,
                 "TEXT_EXTRACTED" if has_text else "NO_TEXT_LAYER",
                 text, sha256_text(text), len(text)),
            )
            acc.total_pages_extracted += 1
            zb["pages"] += 1
            if has_text:
                acc.pages_with_text += 1
                zb["pages_with_text"] += 1
            else:
                acc.pages_no_text += 1
                zb["pages_no_text"] += 1

        language = detect_language("".join(texts))
        self.cur.execute(
            "insert into epidemic_kg.kg_document_versions "
            "(document_version_id, work_id, fulltext_sha256, source_member_path, "
            " source_inner_path, page_count, text_layer_status, language, parser_name, "
            " parser_version, ocr_engine, ocr_confidence, import_run_id, created_at) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,null,null,%s,now()) on conflict do nothing",
            (stable_uuid("dv", sha), work.work_id if work else None, sha, mp, ip,
             page_count, text_layer_status, language, PARSER_NAME, pypdf.__version__,
             IMPORT_RUN_ID),
        )
        self.dv_sources[sha] = (mp, ip)

        if not any_text:
            acc.no_text_layer_count += 1
            acc.ocr_required_count += 1
            zb["ocr_required"] += 1
            self.add_review("OCR_REQUIRED", stable_uuid("pdftarget", mp, ip),
                            f"no text layer {mp}:{ip}",
                            {"member_path": mp, "inner_path": ip,
                             "fulltext_sha256": sha, "page_count": page_count})

        if work is not None:
            self.ensure_is_version_of_edge(doc_nid, self.ensure_work_node(work))
            self.linked_work_ids.add(work.work_id)
            acc.fulltext_linked_count += 1
            zb["linked"] += 1
        else:
            acc.orphan_pdf_count += 1
            zb["orphan_pdf"] += 1
            self.add_review("ORPHAN_PDF", stable_uuid("pdftarget", mp, ip),
                            f"no matching work attachment {mp}:{ip}",
                            {"member_path": mp, "inner_path": ip,
                             "fulltext_sha256": sha})

    def flag_orphan_metadata(self):
        """Works with no PDF linked after the run -> ORPHAN_METADATA."""
        self.cur.execute(
            "select work_id, title, zotero_key, metadata_source_member, "
            "coalesce(raw_metadata->>'File Attachments','') <> '' "
            "from epidemic_kg.kg_paper_works "
            "where work_id not in (select work_id from epidemic_kg.kg_document_versions "
            "                      where work_id is not null)"
        )
        n = 0
        for work_id, title, zkey, src, has_att in self.cur.fetchall():
            n += 1
            self.add_review("ORPHAN_METADATA", str(work_id),
                            f"no PDF linked for work {zkey}",
                            {"work_id": str(work_id), "title": title,
                             "zotero_key": zkey, "source_member": src,
                             "has_attachments": bool(has_att)})
        self.acc.orphan_metadata_count = n
        LOG.info("ORPHAN_METADATA flagged: %d works without linked PDF", n)


def run_ingest(staging_members_dir: str, manifest_csv: str, log_every: int = 50) -> Accounting:
    pdfs = load_manifest_pdfs(manifest_csv)
    conn = connect()
    try:
        ing = PdfIngestor(conn)
        ing.acc.pdf_member_count = len(pdfs)
        ing.load_existing()
        amap = build_attachment_map(conn)
        LOG.info("manifest pdfs: %d; attachment map keys: %d", len(pdfs), len(amap))

        by_zip: dict[str, list[dict]] = {}
        for row in pdfs:
            by_zip.setdefault(row["member_path"], []).append(row)
            ing.acc.zip_bucket(row["member_path"])["pdf_members"] += 1

        done = 0
        for member_path in sorted(by_zip, key=lambda m: (int(batch_of(m) or 0), m)):
            rows = by_zip[member_path]
            zip_path = f"{staging_members_dir}/{member_path}"
            LOG.info("=== zip %s: %d pdf members ===", member_path, len(rows))
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
                for row in rows:
                    if row["inner_path"] not in names:
                        ing.acc.hash_mismatch_count += 1
                        ing.acc.zip_bucket(member_path)["hash_mismatch"] += 1
                        ing.add_review("PDF_HASH_MISMATCH",
                                       stable_uuid("pdftarget", member_path, row["inner_path"]),
                                       f"member missing in zip {member_path}:{row['inner_path']}",
                                       {"member_path": member_path, "inner_path": row["inner_path"],
                                        "error_type": "MemberMissing", "stage": "zip_open"})
                    else:
                        ing.process_pdf(zf, row, amap)
                    done += 1
                    if done % log_every == 0:
                        LOG.info("progress: %d/%d pdfs (new=%d dup=%d fail=%d mismatch=%d pages=%d)",
                                 done, len(pdfs), ing.acc.new_document_version_count,
                                 ing.acc.duplicate_exact_count, ing.acc.failed_parse_count,
                                 ing.acc.hash_mismatch_count, ing.acc.total_pages_extracted)
            conn.commit()
            LOG.info("committed zip %s", member_path)

        ing.flag_orphan_metadata()
        conn.commit()
        return ing.acc
    finally:
        conn.close()
