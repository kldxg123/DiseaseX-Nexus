"""Zotero CSV metadata ingest for kg_r3c import runs.

Parses Zotero-export CSVs (UTF-8/UTF-8-SIG/GB18030 auto-detect, comma/semicolon/tab
delimiter sniffing), preserves raw + normalized values, performs entity resolution
(DOI > PMID > URL > title/year/first-author fingerprint) against existing
kg_paper_works, and produces full accounting. Idempotent per import run.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

from .dbutil import norm_text, sha256_json, sha256_text, stable_uuid

PARSER_VERSION = "kg_r3c.csv_ingest.v1"

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def decode_bytes(raw: bytes) -> tuple[str, str, list[str]]:
    warnings = []
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc, warnings
        except UnicodeDecodeError:
            continue
    warnings.append("FORCED_LATIN1_DECODE")
    return raw.decode("latin-1", errors="replace"), "latin-1", warnings


def sniff_dialect(text: str) -> csv.Dialect:
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.strip()


def title_fingerprint(title: str | None, year: str | None, first_author: str | None) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", norm_text(title)).strip()
    y = re.sub(r"[^0-9]", "", str(year or ""))[:4]
    fa = norm_text((first_author or "").split(",")[0].split(";")[0])
    fa = re.sub(r"[^a-z0-9]+", "", fa)
    return f"title:{t}|year:{y}|fa:{fa}"


@dataclass
class WorkRecord:
    zotero_key: str
    item_type: str
    title: str
    year: int | None
    authors: list
    doi: str
    pmid: str
    url: str
    attachments: list
    raw: dict
    source_member: str
    warnings: list = field(default_factory=list)
    identity: str = ""
    identity_method: str = ""

    def compute_identity(self):
        if self.doi:
            self.identity, self.identity_method = f"doi:{self.doi}", "DOI"
        elif self.pmid:
            self.identity, self.identity_method = f"pmid:{self.pmid}", "PMID"
        elif self.url:
            self.identity, self.identity_method = f"url:{norm_text(self.url)}", "URL"
        else:
            fa = self.authors[0] if self.authors else ""
            self.identity = title_fingerprint(self.title, str(self.year or ""), fa)
            self.identity_method = "TITLE_YEAR_AUTHOR"
        return self.identity


def parse_zotero_csv(raw: bytes, source_member: str) -> tuple[list[WorkRecord], dict]:
    text, encoding, warnings = decode_bytes(raw)
    dialect = sniff_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    records, invalid = [], []
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        title = (row.get("Title") or "").strip()
        if not title:
            invalid.append({"line": i, "reason": "MISSING_TITLE", "raw_key": row.get("Key", "")})
            continue
        year_m = re.search(r"(19|20)\d{2}", row.get("Publication Year") or row.get("Date") or "")
        authors = [a.strip() for a in (row.get("Author") or "").split(";") if a.strip()]
        attachments = []
        for att in (row.get("File Attachments") or "").split(";"):
            att = att.strip()
            if att:
                attachments.append(att.replace("\\", "/").rsplit("/", 1)[-1])
        extra = row.get("Extra") or ""
        pmid_m = re.search(r"PMID:\s*(\d+)", extra)
        rec = WorkRecord(
            zotero_key=(row.get("Key") or "").strip(),
            item_type=(row.get("Item Type") or "").strip(),
            title=title,
            year=int(year_m.group(0)) if year_m else None,
            authors=authors,
            doi=normalize_doi(row.get("DOI")),
            pmid=pmid_m.group(1) if pmid_m else "",
            url=(row.get("Url") or "").strip(),
            attachments=attachments,
            raw={k: v for k, v in row.items()},
            source_member=source_member,
            warnings=list(warnings),
        )
        rec.compute_identity()
        records.append(rec)
    meta = {
        "encoding": encoding,
        "delimiter": dialect.delimiter,
        "warnings": warnings,
        "row_count_raw": len(records) + len(invalid),
        "row_count_valid": len(records),
        "row_count_invalid": len(invalid),
        "invalid_rows": invalid,
        "header": reader.fieldnames,
    }
    return records, meta


def import_works(conn, import_run_id: str, member_records: dict[str, list[WorkRecord]]) -> dict:
    """Idempotent upsert of works. Returns accounting dict."""
    accounting = {
        "new_entity_count": 0, "historical_match_count": 0,
        "duplicate_exact_count": 0, "duplicate_alias_count": 0,
        "work_ids": {},
    }
    seen_in_batch: dict[str, str] = {}
    with conn.cursor() as cur:
        for member, records in member_records.items():
            for rec in records:
                work_id = stable_uuid("work", rec.identity)
                # exact duplicate inside this batch?
                if rec.identity in seen_in_batch:
                    accounting["duplicate_exact_count"] += 1
                    accounting["work_ids"][f"{member}:{rec.zotero_key}"] = seen_in_batch[rec.identity]
                    _record_alias(conn, seen_in_batch[rec.identity], rec, import_run_id)
                    continue
                cur.execute("SELECT work_id FROM epidemic_kg.kg_paper_works WHERE normalized_identity=%s",
                            (rec.identity,))
                row = cur.fetchone()
                if row:
                    accounting["historical_match_count"] += 1
                    wid = str(row[0])
                    _record_alias(conn, wid, rec, import_run_id)
                else:
                    wid = work_id
                    cur.execute("""
                        INSERT INTO epidemic_kg.kg_paper_works
                        (work_id, normalized_identity, identity_method, title, publication_year,
                         authors, doi, pmid, url, item_type, zotero_key, metadata_source_member,
                         raw_metadata, first_import_run_id)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (normalized_identity) DO NOTHING
                    """, (wid, rec.identity, rec.identity_method, rec.title, rec.year,
                          json.dumps(rec.authors, ensure_ascii=False), rec.doi or None,
                          rec.pmid or None, rec.url or None, rec.item_type, rec.zotero_key,
                          rec.source_member,
                          json.dumps(rec.raw, ensure_ascii=False), import_run_id))
                    accounting["new_entity_count"] += 1
                seen_in_batch[rec.identity] = wid
                accounting["work_ids"][f"{member}:{rec.zotero_key}"] = wid
    return accounting


def _record_alias(conn, work_id: str, rec: WorkRecord, import_run_id: str):
    """Keep duplicate-alias membership + judgement basis (no silent drops)."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO epidemic_kg.kg_review_items
            (review_item_id, item_kind, target_id, reason, payload, import_run_id)
            VALUES (%s,'DUPLICATE_ALIAS',%s,%s,%s::jsonb,%s)
            ON CONFLICT (item_kind, target_id, reason, import_run_id) DO NOTHING
        """, (stable_uuid("alias", work_id, rec.source_member, rec.zotero_key),
              work_id, f"alias from {rec.source_member}:{rec.zotero_key}",
              json.dumps({"zotero_key": rec.zotero_key, "title": rec.title,
                          "identity": rec.identity, "identity_method": rec.identity_method,
                          "source_member": rec.source_member}, ensure_ascii=False),
              import_run_id))
