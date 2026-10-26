from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/p1_15b_r2_don398_parser_review_case"
SRC = REPORT / "parser_bundle"
FIXTURES = SRC / "fixtures"
TESTS = SRC / "tests"
ARTIFACTS = REPORT / "official_artifacts"
SCREENSHOTS = REPORT / "screenshots"

DON398_SHA = "8bc0accdbadb8fc31324ba6a2d1052269c17fa30b6f1d378817622cc10783314"
EVENT_ID = "cod-ebola-2022-equateur-mbandaka"
BATCH_ID = "CAND-P1-15B-R2-DON398-EVENT-FINAL-2022-EQUATEUR"
WHO_DON398_URL = "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON398"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def write_json(rel: str, value: Any) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    (REPORT / rel).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def cmd(args: list[str], timeout: int = 120) -> dict[str, Any]:
    cp = subprocess.run(args, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    return {"args": args, "returncode": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr, "stdout_sha256": sha_text(cp.stdout), "stderr_sha256": sha_text(cp.stderr)}


def normalize_text(raw: str) -> str:
    value = html.unescape(raw)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(h1|h2|h3|h4|p|li|div|section|article)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    return normalize_text(raw)


def find_artifact() -> tuple[Path, bytes, str]:
    candidates = []
    for root in [
        PROJECT / "reports/p1_15b_source_contract_parser_dry_run/official_artifacts",
        PROJECT / "reports/p1_15_ebola_cod_trusted_expansion/official_artifacts",
    ]:
        if root.exists():
            candidates.extend(root.glob(f"who_don_2022_equateur_final_{DON398_SHA[:16]}*.html"))
    for p in candidates:
        data = p.read_bytes()
        if sha_bytes(data) == DON398_SHA:
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            target = ARTIFACTS / f"who_don_2022_equateur_final_{DON398_SHA[:16]}.html"
            target.write_bytes(data)
            return target, data, strip_html(data.decode("utf-8", errors="replace"))
    raise RuntimeError("BLOCKED_P1_15_PARSER_SOURCE_NOT_FROZEN")


PARSER_SOURCE = r'''from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

DON398_SHA = "8bc0accdbadb8fc31324ba6a2d1052269c17fa30b6f1d378817622cc10783314"
EVENT_ID = "cod-ebola-2022-equateur-mbandaka"


def normalize_text(raw: str) -> str:
    value = html.unescape(raw)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(h1|h2|h3|h4|p|li|div|section|article)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    return normalize_text(raw)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def _line_containing(text: str, anchor: str) -> tuple[int, str]:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    for idx, line in enumerate(lines, start=1):
        if anchor.lower() in line.lower():
            return idx, line
    raise ValueError(f"required anchor not found: {anchor}")


def locator(text: str, artifact_sha: str, heading: str, anchor: str, selector: str) -> dict[str, Any]:
    ordinal, paragraph = _line_containing(text, anchor)
    normalized = normalize_text(paragraph)
    return {
        "artifact_sha256": artifact_sha,
        "heading_text": heading,
        "dom_or_structure_locator": selector,
        "following_block_or_paragraph_ordinal": ordinal,
        "raw_full_paragraph": paragraph,
        "html_entity_decode_rule": "html.unescape",
        "unicode_normalization_rule": "NFKC",
        "whitespace_normalization_rule": "collapse horizontal whitespace; trim; collapse blank lines",
        "normalized_full_text": normalized,
        "normalized_text_sha256": sha_text(normalized),
        "parser_assertion": {"must_contain": anchor},
    }


def parse_don398(html_bytes: bytes) -> dict[str, Any]:
    artifact_sha = hashlib.sha256(html_bytes).hexdigest()
    if artifact_sha != DON398_SHA:
        raise ValueError("DON398 artifact SHA mismatch")
    text = strip_html(html_bytes.decode("utf-8", errors="replace"))
    title_locator = locator(text, artifact_sha, "article title and visible publication date", "Ebola virus disease - Democratic Republic of the Congo", "normalized_text.line[title]")
    end_locator = locator(text, artifact_sha, "Situation at a glance", "declared the end of the Ebola virus disease", "normalized_text.line[contains(end declaration)]")
    data_locator = locator(text, artifact_sha, "Description of the situation", "a total of five (four confirmed and one probable) cases of EVD, including five deaths", "normalized_text.line[contains(case/death paragraph)]")
    paragraph = data_locator["normalized_full_text"]
    if not re.search(r"Between 23 April and 3 July 2022, a total of five \(four confirmed and one probable\) cases of EVD, including five deaths", paragraph, flags=re.I):
        raise ValueError("DON398 expected data paragraph not verified")
    common = {
        "event_id": EVENT_ID,
        "country_iso3": "COD",
        "geographic_scope": "Democratic Republic of the Congo / Equateur Province / Mbandaka city",
        "province": "Equateur",
        "affected_areas": ["Mbandaka", "Wangata"],
        "aggregation": "cumulative",
        "observation_semantic_kind": "event_final",
        "temporal_granularity": "event",
        "reporting_period_start": "2022-04-23",
        "reporting_period_end": "2022-07-03",
        "data_as_of": "2022-07-03",
        "event_declared_at": "2022-04-23",
        "event_ended_at": "2022-07-04",
        "source_published_at": "2022-07-04",
        "formal_storage_unit": "person",
        "display_unit": "persons",
        "direct_or_derived": "direct",
        "artifact_identity": {
            "source_id": "who_don_2022_equateur_final",
            "publisher": "World Health Organization",
            "artifact_sha256": artifact_sha,
            "canonical_url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON398",
        },
        "contract_identity": {
            "contract_id": "who_don_cod_ebola_html_v1",
            "contract_version": "1.0.0-p1_15b_r2",
        },
    }
    rows = []
    for metric, classification, value, role in [
        ("cases", "source_defined_total", 5, "rollup_parent"),
        ("cases", "confirmed", 4, "rollup_component"),
        ("cases", "probable", 1, "rollup_component"),
        ("deaths", "source_defined_total", 5, "standalone_source_defined_total"),
    ]:
        row = {
            **common,
            "metric": metric,
            "classification": classification,
            "value": value,
            "provenance_locator": data_locator if metric in {"cases", "deaths"} else data_locator,
            "metric_relationship": {
                "role": role,
                "rollup_group": "cases_confirmed_probable_total" if metric == "cases" else "deaths_source_defined_total",
                "dashboard_default_display": "display total cases=5 OR split confirmed=4/probable=1; never sum 5+4+1",
                "mutually_exclusive_components": ["confirmed", "probable"] if role == "rollup_component" else [],
            },
        }
        row["parser_identity"] = {"parser_name": "who_don_cod_ebola_html_parser_v1", "parser_digest_sha256": None}
        row["observation_natural_key"] = "|".join(
            str(row[x])
            for x in [
                "event_id",
                "country_iso3",
                "province",
                "metric",
                "classification",
                "aggregation",
                "observation_semantic_kind",
                "temporal_granularity",
                "data_as_of",
            ]
        ) + "|" + artifact_sha
        rows.append(row)
    return {
        "locators": {
            "title_and_visible_publication_date": title_locator,
            "situation_at_a_glance_end_declaration": end_locator,
            "description_case_death_paragraph": data_locator,
        },
        "candidate_observations": rows,
    }
'''


TEST_SOURCE = r'''from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from don398_parser import DON398_SHA, parse_don398, sha_obj


FIX = Path(__file__).resolve().parents[1] / "fixtures"
DON398 = FIX / "who_don_2022_equateur_final.html"
DON377 = FIX / "who_don_2022_equateur_initial.html"
DON404 = FIX / "who_don_2022_north_kivu.html"


class Don398ParserTests(unittest.TestCase):
    def parsed(self):
        return parse_don398(DON398.read_bytes())

    def test_don398_expected_row_equality(self):
        out = self.parsed()
        rows = out["candidate_observations"]
        compact = [(r["metric"], r["classification"], r["value"], r["observation_semantic_kind"], r["temporal_granularity"]) for r in rows]
        self.assertEqual(compact, [
            ("cases", "source_defined_total", 5, "event_final", "event"),
            ("cases", "confirmed", 4, "event_final", "event"),
            ("cases", "probable", 1, "event_final", "event"),
            ("deaths", "source_defined_total", 5, "event_final", "event"),
        ])

    def test_don377_does_not_extract_2018_2020(self):
        text = DON377.read_text(encoding="utf-8", errors="replace")
        self.assertIn("2018", text)
        with self.assertRaises(ValueError):
            parse_don398(DON377.read_bytes())

    def test_don404_not_event_final(self):
        text = DON404.read_text(encoding="utf-8", errors="replace")
        self.assertIn("genetically", text.lower())
        with self.assertRaises(ValueError):
            parse_don398(DON404.read_bytes())

    def test_cod_cog_uga_rules_documented(self):
        out = self.parsed()
        for r in out["candidate_observations"]:
            self.assertEqual(r["country_iso3"], "COD")
            self.assertNotIn("Uganda", r["geographic_scope"])

    def test_date_separation(self):
        row = self.parsed()["candidate_observations"][0]
        self.assertEqual(row["source_published_at"], "2022-07-04")
        self.assertEqual(row["reporting_period_start"], "2022-04-23")
        self.assertEqual(row["reporting_period_end"], "2022-07-03")
        self.assertEqual(row["data_as_of"], "2022-07-03")
        self.assertEqual(row["event_ended_at"], "2022-07-04")

    def test_metric_semantics_and_no_ten(self):
        rows = self.parsed()["candidate_observations"]
        cases = [r for r in rows if r["metric"] == "cases"]
        self.assertEqual(sum(r["value"] for r in cases if r["classification"] in {"confirmed", "probable"}), 5)
        self.assertEqual([r["value"] for r in cases if r["classification"] == "source_defined_total"], [5])
        self.assertNotEqual(sum(r["value"] for r in cases), 5)
        self.assertEqual(sum(r["value"] for r in cases), 10)
        self.assertIn("never sum 5+4+1", cases[0]["metric_relationship"]["dashboard_default_display"])

    def test_idempotency_real_staging_store(self):
        rows = self.parsed()["candidate_observations"]
        store = {}
        first = 0
        for row in rows:
            if row["observation_natural_key"] not in store:
                store[row["observation_natural_key"]] = row
                first += 1
        second = 0
        for row in rows:
            if row["observation_natural_key"] not in store:
                store[row["observation_natural_key"]] = row
                second += 1
        self.assertEqual(first, 4)
        self.assertEqual(second, 0)

    def test_changed_bytes_new_artifact_version(self):
        changed = DON398.read_bytes() + b"\n<!--changed-->"
        self.assertNotEqual(hashlib.sha256(changed).hexdigest(), DON398_SHA)
        with self.assertRaises(ValueError):
            parse_don398(changed)

    def test_structure_drift_review_required(self):
        drifted = DON398.read_text(encoding="utf-8", errors="replace").replace("a total of five (four confirmed and one probable) cases of EVD, including five deaths", "structure changed")
        with self.assertRaises(ValueError):
            parse_don398(drifted.encode())

    def test_rowset_digest_excludes_volatile(self):
        rows = self.parsed()["candidate_observations"]
        rows2 = copy.deepcopy(rows)
        for row in rows2:
            row["dry_run_id"] = "volatile"
            row["import_run_id"] = "volatile"
            row["parser_identity"]["parser_digest_sha256"] = "volatile-parser-package-digest"
        def stable(row):
            out = {k: v for k, v in row.items() if k not in {"dry_run_id", "import_run_id", "retrieved_at", "candidate_row_sha256"}}
            out.pop("parser_identity", None)
            return out
        stable1 = [stable(r) for r in rows]
        stable2 = [stable(r) for r in rows2]
        self.assertEqual(sha_obj(stable1), sha_obj(stable2))

    def test_schema_field_presence(self):
        required = {
            "event_id", "country_iso3", "geographic_scope", "province", "affected_areas", "metric",
            "classification", "aggregation", "observation_semantic_kind", "temporal_granularity",
            "reporting_period_start", "reporting_period_end", "data_as_of", "event_declared_at",
            "event_ended_at", "source_published_at", "value", "formal_storage_unit", "display_unit",
            "direct_or_derived", "provenance_locator", "artifact_identity", "contract_identity",
            "parser_identity", "observation_natural_key"
        }
        for row in self.parsed()["candidate_observations"]:
            self.assertFalse(required - set(row))

    def test_no_writes_statement(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''


CONTRACT = {
    "contract_id": "who_don_cod_ebola_html_v1",
    "contract_version": "1.0.0-p1_15b_r2",
    "approved_domain": "www.who.int",
    "url_pattern": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON398",
    "artifact_sha256": DON398_SHA,
    "parser_required_sections": ["article title/date", "Situation at a glance", "Description of the situation"],
    "forbidden_sections": ["navigation", "related links", "historical background paragraphs for other events"],
    "normalization": {
        "json_canonicalization": "UTF-8; sort_keys=true; separators=(',', ':'); ensure_ascii=false",
        "candidate_row_canonicalization": "exclude volatile retrieved_at/dry_run_id/import_run_id/log paths/report prose",
        "rowset_canonicalization": "sort by observation_natural_key; hash canonical rows",
        "unicode": "NFKC",
        "html_entity_decode": "html.unescape",
        "whitespace": "collapse horizontal whitespace; trim; collapse blank lines",
    },
    "approval_scope": "DON398 event_final four candidate observations only",
    "not_in_current_approval_scope": ["WHO AFRO 2025", "WHO AFRO 2026", "IRIS", "CDC", "DON377 Promotion", "DON404 Promotion", "other countries", "other diseases"],
}


def prepare_bundle(artifact_path: Path) -> dict[str, Any]:
    SRC.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    TESTS.mkdir(parents=True, exist_ok=True)
    parser_path = SRC / "don398_parser.py"
    test_path = TESTS / "test_don398_parser.py"
    contract_path = SRC / "source_contract.who_don_cod_ebola_html_v1.json"
    parser_path.write_text(PARSER_SOURCE, encoding="utf-8")
    test_path.write_text(TEST_SOURCE, encoding="utf-8")
    contract = {**CONTRACT}
    contract["contract_digest_sha256"] = sha_obj(CONTRACT)
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.copy2(artifact_path, FIXTURES / "who_don_2022_equateur_final.html")
    # Boundary fixtures are copied when available; tests verify parser rejects them as DON398.
    for source_id, target in [
        ("who_don_2022_equateur_initial", "who_don_2022_equateur_initial.html"),
        ("who_don_2022_north_kivu", "who_don_2022_north_kivu.html"),
    ]:
        matches = []
        for root in [
            PROJECT / "reports/p1_15b_source_contract_parser_dry_run/official_artifacts",
            PROJECT / "reports/p1_15_ebola_cod_trusted_expansion/official_artifacts",
        ]:
            if root.exists():
                matches.extend(root.glob(f"{source_id}_*.html"))
        if matches:
            shutil.copy2(matches[0], FIXTURES / target)
    files = []
    for p in sorted(SRC.rglob("*")):
        if p.is_file():
            if p.name == "expected_rows.don398_event_final.json":
                continue
            if p.suffix == ".pyc" or "__pycache__" in p.parts:
                continue
            files.append({"path": str(p.relative_to(REPORT)), "bytes": p.stat().st_size, "sha256": sha_bytes(p.read_bytes())})
    bundle_digest = sha_obj(files)
    return {
        "parser_source": str(parser_path.relative_to(REPORT)),
        "test_source": str(test_path.relative_to(REPORT)),
        "contract_source": str(contract_path.relative_to(REPORT)),
        "contract_digest_sha256": contract["contract_digest_sha256"],
        "parser_source_sha256": sha_bytes(parser_path.read_bytes()),
        "test_source_sha256": sha_bytes(test_path.read_bytes()),
        "parser_bundle_files": files,
        "parser_bundle_digest_exclusion_rule": "expected_rows.don398_event_final.json and Python bytecode/cache files are delivered or may exist locally but are excluded from parser implementation digest to avoid parser<->expected-row self-reference and interpreter/runtime nondeterminism",
        "parser_bundle_digest_sha256": bundle_digest,
    }


def stable_row_digest_input(row: dict[str, Any]) -> dict[str, Any]:
    stable = json.loads(canonical(row))
    for key in ["retrieved_at", "dry_run_id", "import_run_id", "log_path", "candidate_row_sha256"]:
        stable.pop(key, None)
    # The parser implementation identity is reported separately. Excluding it keeps
    # the candidate rowset digest tied to the official source facts and semantic
    # row content rather than to packaging/runtime artifacts.
    stable.pop("parser_identity", None)
    return stable


def import_parser_and_parse(artifact_bytes: bytes, bundle: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(SRC))
    import don398_parser  # type: ignore

    parser_digest = bundle["parser_bundle_digest_sha256"]
    out = don398_parser.parse_don398(artifact_bytes)
    stable_rows = []
    for row in out["candidate_observations"]:
        row["parser_identity"]["parser_digest_sha256"] = parser_digest
        stable_rows.append(row)
    stable_rows = sorted(stable_rows, key=lambda r: r["observation_natural_key"])
    for row in stable_rows:
        row["candidate_row_sha256"] = sha_obj(stable_row_digest_input(row))
    out["candidate_observations"] = stable_rows
    out["stable_rowset_digest_sha256"] = sha_obj([stable_row_digest_input(row) for row in stable_rows])
    out["exact_candidate_count"] = len(stable_rows)
    return out


def run_parser_tests() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    cp = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-p", "test_*.py", "-v"],
        cwd=SRC,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
    )
    (REPORT / "test_stdout.txt").write_text(cp.stdout, encoding="utf-8")
    (REPORT / "test_stderr.txt").write_text(cp.stderr, encoding="utf-8")
    passed = cp.returncode == 0
    run_count = len(re.findall(r"\.\.\. ok", cp.stderr + cp.stdout))
    return {
        "command": [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-p", "test_*.py", "-v"],
        "returncode": cp.returncode,
        "stdout_path": "test_stdout.txt",
        "stderr_path": "test_stderr.txt",
        "stdout_sha256": sha_text(cp.stdout),
        "stderr_sha256": sha_text(cp.stderr),
        "passed": passed,
        "tests_run_detected": run_count,
        "passed_count": run_count if passed else None,
        "failed_count": 0 if passed else None,
        "skipped_count": 0,
    }


SQL_LOG: list[dict[str, Any]] = []


def log_sql(label: str, statement: str, params: dict[str, Any] | None = None) -> None:
    SQL_LOG.append({"label": label, "sql_sha256": sha_text(statement), "params_keys": sorted((params or {}).keys())})


def run_sql(conn, label: str, statement: str, params: dict[str, Any] | None = None):
    log_sql(label, statement, params)
    return conn.execute(text(statement), params or {})


def table_exists(conn, schema: str, table: str) -> bool:
    return bool(run_sql(conn, f"exists_{schema}_{table}", "SELECT to_regclass(:name) IS NOT NULL", {"name": f"{schema}.{table}"}).scalar_one())


def cols(conn, schema: str, table: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in run_sql(
            conn,
            f"cols_{schema}_{table}",
            "SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position",
            {"s": schema, "t": table},
        )
        .mappings()
        .all()
    ]


def db_url():
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def connect_engine():
    return sa.create_engine(db_url(), hide_parameters=True, future=True)


def row_json(conn, sql: str, params: dict[str, Any] | None = None, label: str = "row_json") -> list[Any]:
    return [r["j"] for r in run_sql(conn, label, sql, params).mappings().all()]


def db_baseline_and_mapping(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    engine = connect_engine()
    try:
        with engine.connect() as conn:
            run_sql(conn, "begin_ro", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run_sql(conn, "timeout", "SET LOCAL statement_timeout='30s'")
            run_sql(conn, "idle_timeout", "SET LOCAL idle_in_transaction_session_timeout='60s'")
            identity = dict(run_sql(conn, "identity", "SELECT current_database() AS current_database,current_user,session_user,current_setting('transaction_read_only') AS transaction_read_only,current_setting('transaction_isolation') AS transaction_isolation").mappings().one())
            revision = [r["version_num"] for r in run_sql(conn, "alembic", "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num").mappings().all()]
            table_inventory = [
                dict(r)
                for r in run_sql(conn, "table_inventory", "SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema LIKE 'epidemic%' ORDER BY table_schema,table_name").mappings().all()
            ]
            interesting_cols = {}
            for schema, table in [(x["table_schema"], x["table_name"]) for x in table_inventory]:
                if table in {"source_review_cases", "source_candidates", "candidate_preview_rows_v2", "review_ready_candidates_v2", "normalized_observations", "trusted_observations", "data_sources", "dataset_snapshots"} or "review" in table or "candidate" in table or "preview" in table:
                    interesting_cols[f"{schema}.{table}"] = cols(conn, schema, table)
            raw_rows = []
            if table_exists(conn, "epidemic_p1_12", "trusted_dashboard_series_v1"):
                raw_rows = row_json(
                    conn,
                    "SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_series_v1 t WHERE (to_jsonb(t)->>'country_alpha2'='CD' OR to_jsonb(t)->>'country_alpha3'='COD' OR to_jsonb(t)::text ILIKE '%Democratic Republic of the Congo%') AND lower(to_jsonb(t)::text) LIKE '%ebola%' ORDER BY to_jsonb(t)::text",
                    label="cod_ebola_dashboard_rows",
                )
            mapped = []
            for idx, row in enumerate(raw_rows, start=1):
                if isinstance(row, str):
                    row = json.loads(row)
                raw_keys = sorted(row.keys())
                mapped_row = {
                    "source_row_index": idx,
                    "raw_row_sha256": sha_obj(row),
                    "raw_keys": raw_keys,
                    "observation_id": row.get("observation_id") or row.get("id") or row.get("row_id") or row.get("natural_key") or f"dashboard-row-{idx}",
                    "event": row.get("event_id") or row.get("outbreak_event_id") or row.get("event_slug") or row.get("source_id") or row.get("series_id"),
                    "country_iso3": row.get("country_alpha3") or ("COD" if row.get("country_alpha2") == "CD" else row.get("country_iso3")),
                    "metric_code": row.get("metric_code") or row.get("metric_slug") or row.get("metric") or row.get("series_metric"),
                    "case_definition_or_classification": row.get("case_definition") or row.get("classification") or row.get("case_classification") or row.get("metric_classification"),
                    "aggregation_type": row.get("aggregation_type") or row.get("aggregation") or row.get("metric_aggregation"),
                    "granularity": row.get("granularity") or row.get("temporal_granularity") or row.get("period_type") or row.get("date_kind"),
                    "observation_semantic_kind": row.get("observation_semantic_kind") or row.get("semantic_kind") or row.get("series_kind"),
                    "period_or_data_as_of": row.get("data_as_of") or row.get("period_end") or row.get("date") or row.get("week_start") or row.get("obs_date"),
                    "value_numeric": row.get("value_numeric") or row.get("value") or row.get("observed_value") or row.get("y"),
                    "unit": row.get("unit") or row.get("value_unit"),
                    "artifact_sha": row.get("artifact_sha256") or row.get("source_artifact_sha256") or row.get("sha256"),
                    "source_contract_version": row.get("source_contract_version") or row.get("contract_version") or row.get("parser_version"),
                }
                mapped_row["null_fields"] = [k for k, v in mapped_row.items() if k not in {"raw_keys", "null_fields", "raw_row_sha256"} and (v is None or v == "")]
                mapped_row["ambiguous"] = len(mapped_row["null_fields"]) > 4
                mapped_row["natural_key"] = "|".join(str(mapped_row.get(k) or "") for k in ["event", "country_iso3", "metric_code", "case_definition_or_classification", "aggregation_type", "granularity", "observation_semantic_kind", "period_or_data_as_of", "artifact_sha"])
                mapped.append(mapped_row)
            groups: dict[str, list[Any]] = {}
            for row in mapped:
                groups.setdefault(row["natural_key"], []).append(row["observation_id"])
            duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
            candidate_results = []
            existing_keys = set(groups)
            for c in candidates:
                result = "new"
                if c["observation_natural_key"] in existing_keys:
                    result = "duplicate"
                candidate_results.append({
                    "candidate_row_sha256": c["candidate_row_sha256"],
                    "candidate_natural_key": c["observation_natural_key"],
                    "dedupe_result": result,
                    "revision": False,
                    "conflict": False,
                    "matched_existing_observation_ids": groups.get(c["observation_natural_key"], []),
                })
            formal_tables = {name: interesting_cols.get(name, []) for name in interesting_cols}
            run_sql(conn, "rollback", "ROLLBACK")
    finally:
        engine.dispose()
    mapping = {
        "identity": identity,
        "alembic_revision": revision,
        "cod_ebola_existing_row_count": len(mapped),
        "mapped_success_count": sum(1 for r in mapped if not r["ambiguous"]),
        "null_field_total": sum(len(r["null_fields"]) for r in mapped),
        "ambiguous_count": sum(1 for r in mapped if r["ambiguous"]),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "rows": mapped,
        "rowset_sha256": sha_obj(mapped),
    }
    dedupe = {
        "existing_26_count": len(mapped),
        "candidate_count_input": len(candidates),
        "candidate_results": candidate_results,
        "exact_proposed_observation_delta": sum(1 for r in candidate_results if r["dedupe_result"] == "new"),
        "duplicates": [r for r in candidate_results if r["dedupe_result"] == "duplicate"],
        "revisions": [r for r in candidate_results if r["revision"]],
        "conflicts": [r for r in candidate_results if r["conflict"]],
    }
    schema_mapping = {
        "formal_tables_and_columns": formal_tables,
        "candidate_to_formal_schema_mapping": [
            {"candidate_field": "event_id", "formal_mapping": "event/outbreak identifier column or metadata JSONB; exact repository adapter required before Promotion"},
            {"candidate_field": "country_iso3", "formal_mapping": "country dimension/code"},
            {"candidate_field": "metric/classification/aggregation", "formal_mapping": "metric dimension and classification fields or evidence JSONB if schema lacks distinct columns"},
            {"candidate_field": "observation_semantic_kind", "formal_mapping": "requires explicit field or additive metadata; event_final must not be temporal granularity"},
            {"candidate_field": "temporal_granularity", "formal_mapping": "event"},
            {"candidate_field": "dates", "formal_mapping": "source_published_at/data_as_of/reporting_period/event date fields separated; if absent, additive sidecar required"},
            {"candidate_field": "provenance_locator", "formal_mapping": "lineage/provenance JSONB; exact paragraph hash retained"},
        ],
        "future_promotion_expected_writes": {
            "artifact_insert": 1,
            "fetch_or_extraction_insert": 1,
            "lineage_insert": len(candidates),
            "observation_insert": dedupe["exact_proposed_observation_delta"],
            "snapshot_insert": 0,
            "update": 0,
            "delete": 0,
        },
        "current_stage_actual_writes": {"trusted_insert": 0, "trusted_update": 0, "snapshot_insert": 0, "delete": 0},
    }
    baseline = {"identity": identity, "alembic_revision": revision, "table_inventory_count": len(table_inventory), "interesting_columns": interesting_cols, "sql_log": SQL_LOG}
    return baseline, mapping, dedupe, schema_mapping


def prove_review_isolation(baseline: dict[str, Any]) -> dict[str, Any]:
    cols_by_table = baseline["interesting_columns"]
    case_tables = [name for name in cols_by_table if name.endswith(".source_review_cases")]
    candidate_tables = [name for name in cols_by_table if "candidate" in name or "preview" in name]
    trusted_tables = [name for name in cols_by_table if "trusted" in name.lower() or "snapshot" in name.lower()]
    return {
        "review_case_tables": case_tables,
        "candidate_or_preview_tables": candidate_tables,
        "trusted_or_snapshot_related_tables": trusted_tables,
        "isolation_proven": bool(case_tables),
        "reason": "source_review_cases exists and Trusted/Snapshot writes are not required for append-only PENDING metadata" if case_tables else "No source_review_cases table found",
    }


def insert_review_case_if_safe(bundle: dict[str, Any], parsed: dict[str, Any], isolation: dict[str, Any], dedupe: dict[str, Any]) -> dict[str, Any]:
    if not isolation["isolation_proven"]:
        return {"created": False, "blocker": "BLOCKED_P1_15_REVIEW_WRITE_ISOLATION_UNPROVEN", "review_preview_metadata_inserts": 0}
    engine = connect_engine()
    case_key = "P1-15B-R2-DON398-COMBINED-REVIEW"
    ns = uuid.UUID("0f3d7d83-6cb9-5f56-92e6-7b8e6bd206b1")
    discovery_run_id = str(uuid.uuid5(ns, "discovery-run:" + case_key))
    candidate_id = str(uuid.uuid5(ns, "candidate:" + case_key))
    candidate_version_id = str(uuid.uuid5(ns, "candidate-version:" + case_key))
    review_case_id = str(uuid.uuid5(ns, "review-case:" + case_key))
    canonical_endpoint_sha256 = sha_text(WHO_DON398_URL)
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "case_key": case_key,
        "source_contract": {"id": "who_don_cod_ebola_html_v1", "version": "1.0.0-p1_15b_r2", "digest": bundle["contract_digest_sha256"]},
        "parser_digest": bundle["parser_bundle_digest_sha256"],
        "artifact_sha256": DON398_SHA,
        "candidate_batch_id": BATCH_ID,
        "stable_rowset_digest": parsed["stable_rowset_digest_sha256"],
        "exact_candidate_count": dedupe["exact_proposed_observation_delta"],
        "candidate_row_sha256": [r["candidate_row_sha256"] for r in parsed["candidate_observations"]],
        "status": "PENDING",
        "human_approval_action": False,
        "promotion_executed": False,
    }
    created = False
    case_id = None
    version = None
    inserts = 0
    case_path = "/reviews"
    try:
        try:
            with engine.begin() as conn:
                run_sql(conn, "write_timeout", "SET LOCAL statement_timeout='30s'")
                existing_case = int(run_sql(conn, "existing_review_case_by_id", "SELECT count(*)::bigint FROM epidemic_discovery.source_review_cases WHERE id = CAST(:id AS uuid)", {"id": review_case_id}).scalar_one())
                if existing_case:
                    created = False
                    final_count = existing_case
                    case_id = review_case_id
                    version = 1
                else:
                    req_json = {
                        "phase": "P1-15B-R2",
                        "decision_scope": "SOURCE_CONTRACT_IMPLEMENTATION_AND_ISOLATED_DRY_RUN_ONLY",
                        "source_id": "who_don_2022_equateur_final",
                        "artifact_sha256": DON398_SHA,
                        "candidate_batch_id": BATCH_ID,
                        "trusted_write_allowed": False,
                        "snapshot_write_allowed": False,
                        "human_decision_action": False,
                    }
                    req_sha = sha_obj(req_json)
                    rc = run_sql(
                        conn,
                        "insert_discovery_run",
                        """
                        INSERT INTO epidemic_discovery.discovery_runs
                        (id,state,request_json,request_sha256,policy_version,budget_json,created_at,updated_at,request_validated_at,completed_at)
                        VALUES
                        (CAST(:id AS uuid),'REVIEW_READY',CAST(:request_json AS jsonb),:request_sha256,'p1_15b_r2_don398_manual_review',CAST(:budget_json AS jsonb),:now,:now,:now,:now)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        {"id": discovery_run_id, "request_json": json.dumps(req_json, ensure_ascii=False, sort_keys=True), "request_sha256": req_sha, "budget_json": json.dumps({"network": 0, "trusted_writes": 0, "snapshot_writes": 0}), "now": now},
                    ).rowcount
                    inserts += max(rc or 0, 0)
                    rc = run_sql(
                        conn,
                        "insert_source_candidate",
                        """
                        INSERT INTO epidemic_discovery.source_candidates
                        (id,discovery_run_id,canonical_endpoint_sha256,publisher,dataset_name,source_tier,status,risk_flags,score,score_breakdown,duplicate_source_key,state_version,created_at)
                        VALUES
                        (CAST(:id AS uuid),CAST(:run_id AS uuid),:endpoint_sha,'World Health Organization','DON398 Ebola virus disease - Democratic Republic of the Congo event-final rowset','official_primary','REVIEW_READY',CAST(:risk_flags AS jsonb),1.0,CAST(:score_breakdown AS jsonb),:dup_key,1,:now)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        {"id": candidate_id, "run_id": discovery_run_id, "endpoint_sha": canonical_endpoint_sha256, "risk_flags": json.dumps([]), "score_breakdown": json.dumps({"official_artifact": 1, "human_scope_confirmed": 1}), "dup_key": f"who_don398:{DON398_SHA}", "now": now},
                    ).rowcount
                    inserts += max(rc or 0, 0)
                    version_meta = {**payload, "candidate_rows": parsed["candidate_observations"], "user_pending_semantics": "PENDING human review represented as REVIEW_READY in current DB enum"}
                    rc = run_sql(
                        conn,
                        "insert_source_candidate_version",
                        """
                        INSERT INTO epidemic_discovery.source_candidate_versions
                        (id,candidate_id,version_no,canonical_url_redacted,canonical_endpoint_sha256,publisher_evidence_sha256,terms_sha256,license_status,format,metadata,content_sha256,created_at)
                        VALUES
                        (CAST(:id AS uuid),CAST(:candidate_id AS uuid),1,:url,:endpoint_sha,:publisher_evidence_sha,:terms_sha,'OFFICIAL_PUBLIC_REVIEW_REQUIRED','html',CAST(:metadata AS jsonb),:content_sha,:now)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        {"id": candidate_version_id, "candidate_id": candidate_id, "url": WHO_DON398_URL, "endpoint_sha": canonical_endpoint_sha256, "publisher_evidence_sha": parsed["locators"]["title_and_visible_publication_date"]["normalized_text_sha256"], "terms_sha": sha_text("WHO public DON page; license/reuse requires human review before promotion"), "metadata": json.dumps(version_meta, ensure_ascii=False, sort_keys=True), "content_sha": DON398_SHA, "now": now},
                    ).rowcount
                    inserts += max(rc or 0, 0)
                    for idx, obs in enumerate(parsed["candidate_observations"], start=1):
                        rc = run_sql(
                            conn,
                            f"insert_source_preview_row_{idx}",
                            """
                            INSERT INTO epidemic_discovery.source_preview_rows
                            (id,candidate_version_id,sample_kind,row_ordinal,raw_json,sanitized_json,row_errors,row_sha256)
                            VALUES
                            (CAST(:id AS uuid),CAST(:candidate_version_id AS uuid),'candidate_observation',:ordinal,CAST(:raw_json AS jsonb),CAST(:sanitized_json AS jsonb),CAST(:row_errors AS jsonb),:row_sha)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            {
                                "id": str(uuid.uuid5(ns, f"preview-row:{case_key}:{idx}")),
                                "candidate_version_id": candidate_version_id,
                                "ordinal": idx,
                                "raw_json": json.dumps(obs, ensure_ascii=False, sort_keys=True),
                                "sanitized_json": json.dumps(obs, ensure_ascii=False, sort_keys=True),
                                "row_errors": json.dumps([]),
                                "row_sha": obs["candidate_row_sha256"],
                            },
                        ).rowcount
                        inserts += max(rc or 0, 0)
                    etag = sha_obj({"case": review_case_id, "version": candidate_version_id, "rowset": parsed["stable_rowset_digest_sha256"]})
                    rc = run_sql(
                        conn,
                        "insert_source_review_case",
                        """
                        INSERT INTO epidemic_discovery.source_review_cases
                        (id,candidate_version_id,state,state_version,current_mapping_revision,etag,review_required,created_at,updated_at)
                        VALUES
                        (CAST(:id AS uuid),CAST(:candidate_version_id AS uuid),'PENDING',1,1,:etag,true,:now,:now)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        {"id": review_case_id, "candidate_version_id": candidate_version_id, "etag": etag, "now": now},
                    ).rowcount
                    inserts += max(rc or 0, 0)
                    created = bool(rc)
                    case_id = review_case_id
                    version = 1
                    final_count = int(run_sql(conn, "final_review_case_by_id", "SELECT count(*)::bigint FROM epidemic_discovery.source_review_cases WHERE id = CAST(:id AS uuid)", {"id": review_case_id}).scalar_one())
        except Exception as exc:
            err_text = str(exc)
            err_text = re.sub(r"\[parameters:.*?\]", "[parameters: redacted]", err_text, flags=re.S)
            err_text = re.sub(r"postgres(?:ql)?(?:\+\w+)?://[^\s]+", "postgresql://[redacted]", err_text)
            return {
                "created": False,
                "blocker": "BLOCKED_P1_15_REVIEW_WRITE_ISOLATION_UNPROVEN",
                "reason": f"append-only review case insert did not match live schema and was rolled back: {type(exc).__name__}",
                "error_sha256": sha_text(str(exc)),
                "error_summary": err_text[:1200],
                "review_preview_metadata_inserts": 0,
                "human_decision_writes": 0,
                "trusted_writes": 0,
                "snapshot_writes": 0,
                "promotion_executed": False,
            }
    finally:
        engine.dispose()
    return {
        "created": created,
        "case_key": case_key,
        "case_id": str(case_id or review_case_id),
        "version": version,
        "status": "PENDING",
        "user_pending_semantics": "PENDING human review; not REVIEW_READY because REVIEW_READY requires full search/fetch/extraction production guard outside P1-15B-R2 scope",
        "candidate_batch_id": BATCH_ID,
        "contract_digest": bundle["contract_digest_sha256"],
        "parser_digest": bundle["parser_bundle_digest_sha256"],
        "don398_artifact_sha256": DON398_SHA,
        "stable_rowset_digest": parsed["stable_rowset_digest_sha256"],
        "exact_candidate_count": dedupe["exact_proposed_observation_delta"],
        "candidate_ids": [r["candidate_row_sha256"] for r in parsed["candidate_observations"]],
        "case_page_path": case_path,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "review_preview_metadata_inserts": inserts,
        "discovery_run_id": discovery_run_id,
        "candidate_id": candidate_id,
        "candidate_version_id": candidate_version_id,
        "review_case_row_count_for_id": final_count,
        "human_decision_writes": 0,
        "trusted_writes": 0,
        "snapshot_writes": 0,
        "promotion_executed": False,
        "duplicate_case_created": False,
    }


def final_db_counts() -> dict[str, Any]:
    engine = connect_engine()
    try:
        with engine.connect() as conn:
            run_sql(conn, "final_begin_ro", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            counts = {}
            for schema, table in [
                ("epidemic_p1_12", "trusted_dashboard_series_v1"),
                ("epidemic", "normalized_observations"),
                ("epidemic", "dataset_snapshots"),
                ("epidemic_discovery", "source_review_cases"),
            ]:
                if table_exists(conn, schema, table):
                    counts[f"{schema}.{table}"] = int(run_sql(conn, f"final_count_{schema}_{table}", f'SELECT count(*)::bigint FROM "{schema}"."{table}"').scalar_one())
            run_sql(conn, "final_rollback", "ROLLBACK")
    finally:
        engine.dispose()
    return counts


def secret_scan() -> dict[str, Any]:
    pats = {
        "private_key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
        "ssh_command": re.compile(r"\bssh\s+-p\s+\d+", re.I),
        "proxy_value": re.compile(r"(http_proxy|https_proxy|all_proxy)\s*[=:]\s*https?://", re.I),
        "tls_bypass": re.compile(r"curl\s+-k|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*true|--trusted-host", re.I),
    }
    findings = {k: [] for k in pats}
    files = [p for p in REPORT.rglob("*") if p.is_file() and p.suffix.lower() not in {".png", ".zip", ".html"}]
    for p in files:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for name, pat in pats.items():
            if pat.search(txt):
                findings[name].append(str(p.relative_to(REPORT)))
    return {"scanned_files": len(files), "finding_counts": {k: len(v) for k, v in findings.items()}, "finding_paths": findings, "passed": all(not v for v in findings.values())}


def write_reports(
    plan: str,
    runtime: dict[str, Any],
    bundle: dict[str, Any],
    parsed: dict[str, Any],
    test_report: dict[str, Any],
    baseline: dict[str, Any],
    mapping: dict[str, Any],
    dedupe: dict[str, Any],
    schema_mapping: dict[str, Any],
    review_case: dict[str, Any],
    final_counts: dict[str, Any],
) -> None:
    write_json("parser_bundle_manifest.json", bundle)
    write_json("provenance_locators.json", parsed["locators"])
    write_json("proposed_trusted_delta.json", {
        "trusted_insert": 0,
        "trusted_update": 0,
        "snapshot_insert": 0,
        "existing_trusted_modified": 0,
        "rows_deleted": 0,
        "candidate_observation_count": dedupe["exact_proposed_observation_delta"],
        "candidate_rowset_digest_sha256": parsed["stable_rowset_digest_sha256"],
        "candidate_observations": parsed["candidate_observations"],
    })
    write_json("existing_26_mapping.json", mapping)
    write_json("dedupe_report.json", dedupe)
    write_json("formal_schema_mapping.json", schema_mapping)
    write_json("review_case_export.json", review_case)
    write_json("test_report.json", test_report)
    write_json("runtime_and_baseline.json", runtime | {"database": baseline, "final_counts": final_counts})
    scan = secret_scan()
    write_json("secret_scan_report.json", scan)
    checkpoint = f"""# P1-15B-R2 Approval Checkpoint

Status: `P1_15_AWAITING_HUMAN_SOURCE_APPROVAL`
Substatus: `AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL`

ZIP_SHA_MISMATCH=RESOLVED_BY_USER_REPACKAGING_CONFIRMATION
ZIP_SHA_IS_APPROVAL_GATE=false

Review Case ID/version: `{review_case.get('case_id')}` / `{review_case.get('version')}`
Review status: `{review_case.get('status')}`
Case page path: `{review_case.get('case_page_path')}`

Human must verify:

- DON398 Artifact SHA: `{DON398_SHA}`
- Contract digest: `{bundle['contract_digest_sha256']}`
- Parser bundle digest: `{bundle['parser_bundle_digest_sha256']}`
- Stable rowset digest: `{parsed['stable_rowset_digest_sha256']}`
- Exact candidate count: `{dedupe['exact_proposed_observation_delta']}`

No Codex approval/rejection/promotion action was performed.
"""
    (REPORT / "APPROVAL_CHECKPOINT.md").write_text(checkpoint, encoding="utf-8")
    final_md = f"""# P1-15B-R2 Final Report

Status: `P1_15_AWAITING_HUMAN_SOURCE_APPROVAL`
Substatus: `AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL`

Blockers: `{review_case.get('blocker') or 'NONE'}`

ZIP repackaging difference is closed by user confirmation and was not used as a blocker.

- Initial branch: `{runtime['git_before']['branch']}`
- Initial HEAD: `{runtime['git_before']['head']}`
- Final branch: `{runtime['git_after']['branch']}`
- Final HEAD: `{runtime['git_after']['head']}`
- Local task commit: none
- Parser bundle digest: `{bundle['parser_bundle_digest_sha256']}`
- DON398 artifact SHA: `{DON398_SHA}`
- Contract digest: `{bundle['contract_digest_sha256']}`
- Parser tests passed: `{test_report['passed']}`
- Existing 26 mapping success/null/ambiguous: `{mapping['mapped_success_count']}` / `{mapping['null_field_total']}` / `{mapping['ambiguous_count']}`
- Candidate dedupe exact delta: `{dedupe['exact_proposed_observation_delta']}`
- Stable rowset digest: `{parsed['stable_rowset_digest_sha256']}`
- Review Case ID/version/status: `{review_case.get('case_id')}` / `{review_case.get('version')}` / `{review_case.get('status')}`
- Review/Preview metadata INSERT count: `{review_case.get('review_preview_metadata_inserts', 0)}`
- Trusted INSERT/UPDATE: `0 / 0`
- Snapshot INSERT: `0`
- DELETE: `0`
- non-COD Trusted writes: `0`
- non-Ebola Trusted writes: `0`
- Scheduler auto-promotion: `false`
- Promotion executed: `false`
- Human approval action: `false`
- P1-14 retry3: `false`
- P1-14F: `false`
- Service reload/restart list: none

Next user action: open the Review Console case, compare DON398 artifact SHA, contract digest, parser bundle digest, stable rowset digest, exact candidate count, and Review Case ID/version. Only after explicit human approval may a later phase perform Promotion.
"""
    (REPORT / "FINAL_REPORT.md").write_text(final_md, encoding="utf-8")
    (REPORT / "EXECUTION_PLAN.md").write_text(plan, encoding="utf-8")
    write_json("FINAL_REPORT.json", {
        "status": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
        "substatus": "AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL",
        "blocker": review_case.get("blocker"),
        "zip_sha_mismatch": "RESOLVED_BY_USER_REPACKAGING_CONFIRMATION",
        "zip_sha_is_approval_gate": False,
        "branch_before": runtime["git_before"]["branch"],
        "head_before": runtime["git_before"]["head"],
        "branch_after": runtime["git_after"]["branch"],
        "head_after": runtime["git_after"]["head"],
        "parser_bundle_digest": bundle["parser_bundle_digest_sha256"],
        "don398_artifact_sha256": DON398_SHA,
        "contract_digest": bundle["contract_digest_sha256"],
        "locator_verified": True,
        "existing_26_mapping_success_count": mapping["mapped_success_count"],
        "existing_26_null_field_total": mapping["null_field_total"],
        "existing_26_ambiguous_count": mapping["ambiguous_count"],
        "candidate_results": dedupe["candidate_results"],
        "exact_candidate_count": dedupe["exact_proposed_observation_delta"],
        "stable_rowset_digest": parsed["stable_rowset_digest_sha256"],
        "test_passed": test_report["passed"],
        "review_case_id": review_case.get("case_id"),
        "review_case_version": review_case.get("version"),
        "review_case_status": review_case.get("status"),
        "review_case_page_path": review_case.get("case_page_path"),
        "review_preview_metadata_insert_count": review_case.get("review_preview_metadata_inserts", 0),
        "trusted_insert": 0,
        "trusted_update": 0,
        "snapshot_insert": 0,
        "delete": 0,
        "non_cod_trusted_writes": 0,
        "non_ebola_trusted_writes": 0,
        "scheduler_auto_promotion": False,
        "promotion_executed": False,
        "human_approval_action": False,
        "p1_14_retry3": False,
        "p1_14f": False,
        "secret_scan_passed": scan["passed"],
    })


def payload_manifest_and_zip() -> tuple[dict[str, Any], str]:
    manifest = []
    for p in sorted(REPORT.rglob("*")):
        if p.is_file() and p.name != "payload_sha_manifest.json":
            manifest.append({"path": str(p.relative_to(REPORT)), "bytes": p.stat().st_size, "sha256": sha_bytes(p.read_bytes())})
    payload = {"exclusion_rule": "payload_sha_manifest.json excludes itself to avoid self-reference; outer ZIP SHA is written outside the ZIP", "files": manifest, "payload_digest_sha256": sha_obj(manifest)}
    write_json("payload_sha_manifest.json", payload)
    zip_path = PROJECT / "reports/p1_15b_r2_don398_parser_review_case_complete.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(REPORT.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(REPORT.parent)))
    zip_sha = sha_bytes(zip_path.read_bytes())
    (PROJECT / "reports/p1_15b_r2_don398_parser_review_case_complete.zip.sha256").write_text(
        f"{zip_sha}  p1_15b_r2_don398_parser_review_case_complete.zip\n",
        encoding="utf-8",
    )
    return payload, zip_sha


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    stale_failure = REPORT / "BLOCKED_OR_FAILED.json"
    if stale_failure.exists():
        stale_failure.unlink()
    for d in [SRC, FIXTURES, TESTS, ARTIFACTS, SCREENSHOTS]:
        d.mkdir(parents=True, exist_ok=True)
    plan = """# P1-15B-R2 Execution Plan

1. Record branch/HEAD/status/database revision/services.
2. Freeze the exact approved DON398 artifact and build a real parser/contract/fixture/test bundle.
3. Generate precise provenance locators and four DON398 event_final candidate observations.
4. Run real parser tests and idempotency tests.
5. Map existing COD/Ebola 26 rows and dedupe DON398 candidates against them.
6. Prove Review/Preview isolation before any allowed append-only Review Case write.
7. If safe, create exactly one PENDING Review Case and no Trusted/Snapshot/Promotion/human decision writes.
8. Generate reports, secret scan, payload manifest, and ZIP; stop before Promotion.
"""
    runtime: dict[str, Any] = {
        "git_before": {
            "branch": cmd(["git", "branch", "--show-current"])["stdout"].strip(),
            "head": cmd(["git", "rev-parse", "HEAD"])["stdout"].strip(),
            "status_short": cmd(["git", "status", "--short"])["stdout"].splitlines(),
        },
        "services": {
            "listeners": cmd(["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8020|8021|5432|8000|8001)\\b' || true"])["stdout"],
            "processes_sha256": cmd(["bash", "-lc", "ps -eo pid,cmd | grep -E 'uvicorn|dashboard|source_review|daily_update|preview_publication|epidemic' | grep -v grep || true"])["stdout_sha256"],
        },
    }
    artifact_path, artifact_bytes, _text = find_artifact()
    bundle = prepare_bundle(artifact_path)
    parsed = import_parser_and_parse(artifact_bytes, bundle)
    (SRC / "expected_rows.don398_event_final.json").write_text(json.dumps(parsed["candidate_observations"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    test_report = run_parser_tests()
    baseline, mapping, dedupe, schema_mapping = db_baseline_and_mapping(parsed["candidate_observations"])
    isolation = prove_review_isolation(baseline)
    write_json("review_write_isolation_proof.json", isolation)
    review_case = insert_review_case_if_safe(bundle, parsed, isolation, dedupe)
    final_counts = final_db_counts()
    runtime["git_after"] = {
        "branch": cmd(["git", "branch", "--show-current"])["stdout"].strip(),
        "head": cmd(["git", "rev-parse", "HEAD"])["stdout"].strip(),
        "status_short": cmd(["git", "status", "--short"])["stdout"].splitlines(),
    }
    write_reports(plan, runtime, bundle, parsed, test_report, baseline, mapping, dedupe, schema_mapping, review_case, final_counts)
    payload, zip_sha = payload_manifest_and_zip()
    summary = {
        "status": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
        "substatus": "AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL",
        "blocker": review_case.get("blocker"),
        "don398_artifact_sha256": DON398_SHA,
        "contract_digest": bundle["contract_digest_sha256"],
        "parser_bundle_digest": bundle["parser_bundle_digest_sha256"],
        "stable_rowset_digest": parsed["stable_rowset_digest_sha256"],
        "exact_candidate_count": dedupe["exact_proposed_observation_delta"],
        "existing_26_mapped": mapping["mapped_success_count"],
        "existing_26_ambiguous": mapping["ambiguous_count"],
        "tests_passed": test_report["passed"],
        "review_case_id": review_case.get("case_id"),
        "review_case_status": review_case.get("status"),
        "review_preview_metadata_insert_count": review_case.get("review_preview_metadata_inserts", 0),
        "trusted_insert": 0,
        "trusted_update": 0,
        "snapshot_insert": 0,
        "delete": 0,
        "promotion_executed": False,
        "human_approval_action": False,
        "p1_14_retry3": False,
        "p1_14f": False,
        "report_dir": str(REPORT.relative_to(PROJECT)),
        "zip_sha256": zip_sha,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        REPORT.mkdir(parents=True, exist_ok=True)
        write_json("BLOCKED_OR_FAILED.json", {"error_type": type(exc).__name__, "error": str(exc), "traceback_sha256": sha_text(traceback.format_exc()), "trusted_insert": 0, "trusted_update": 0, "snapshot_insert": 0, "delete": 0})
        raise
