from __future__ import annotations

from pathlib import Path


def main() -> None:
    p = Path("tools/p1_14e_r2_2.py")
    s = p.read_text(encoding="utf-8")
    start = s.index("def main() -> None:")
    end = s.index('\n\nif __name__ == "__main__":', start)
    new = r'''def write_sidecar(name: str) -> str:
    digest = sha_bytes((OUT / name).read_bytes())
    (OUT / f"{name}.sha256").write_text(digest + f"  {name}\n", encoding="utf-8")
    return digest


def write_evidence_index() -> dict[str, Any]:
    excluded = {
        "evidence_index.r2_2.json",
        "evidence_index.r2_2.json.sha256",
        "remediation_manifest.r2_2.proposed.json",
        "remediation_manifest.r2_2.proposed.json.sha256",
        "report.json",
        "report.md",
        "package_index.r2_2.json",
        "package_index.r2_2.json.sha256",
        "complete_packet.r2_2.zip",
        "complete_packet.r2_2.zip.sha256",
    }
    records = [file_record(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in excluded]
    index = {"schema_version": "p1_14e_r2_2_evidence_index.v1", "self_listed": False, "evidence_files": records}
    write_json("evidence_index.r2_2.json", index)
    index["sha256"] = write_sidecar("evidence_index.r2_2.json")
    return index


def build_package_index(member_names: list[str]) -> dict[str, Any]:
    members = [file_record(OUT / name, role="zip_member") for name in member_names]
    package = {"schema_version": "p1_14e_r2_2_package_index.v1", "self_listed": False, "zip_members": members}
    write_json("package_index.r2_2.json", package)
    package["sha256"] = write_sidecar("package_index.r2_2.json")
    return package


def build_and_verify_zip(member_names: list[str]) -> tuple[str, dict[str, Any]]:
    zip_path = OUT / "complete_packet.r2_2.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in member_names:
            zf.write(OUT / name, arcname=name)
    zip_sha = sha_bytes(zip_path.read_bytes())
    (OUT / "complete_packet.r2_2.zip.sha256").write_text(zip_sha + "  complete_packet.r2_2.zip\n", encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="p1_14e_r2_2_verify_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        checks = []
        ok = True
        for name in member_names:
            original = OUT / name
            extracted = tmp / name
            same = extracted.exists() and original.read_bytes() == extracted.read_bytes() and sha_bytes(extracted.read_bytes()) == sha_bytes(original.read_bytes())
            checks.append({"file": name, "bytes": original.stat().st_size, "sha256": sha_bytes(original.read_bytes()), "verified_after_extract": same})
            ok = ok and same
        observed = sorted(p.name for p in tmp.iterdir() if p.is_file())
        ok = ok and observed == sorted(member_names)
        verification = {"verified": ok, "member_count": len(member_names), "members": checks, "observed_member_names_match": observed == sorted(member_names)}
        return zip_sha, verification
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    if OUT.exists():
        print(json.dumps({"status": "BLOCKED_R2_2_OUTPUT_DIR_ALREADY_EXISTS", "output_dir": str(OUT), "actual_database_writes": 0, "rows_deleted": 0}, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2)
    OUT.mkdir(parents=True)
    evidence = input_evidence()
    code_scan = full_code_scan()
    metadata = metadata_reattestation()
    engine = create_engine(os.environ["APP_DATABASE_URL"], hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            snap_a = snapshot(conn, "snapshot_a")
        with engine.connect() as conn:
            snap_b = snapshot(conn, "snapshot_b")
    finally:
        engine.dispose()

    write_json("input_evidence_hashes.json", evidence)
    write_json("database_snapshot_a.json", {k: v for k, v in snap_a.items() if k != "catalog"})
    write_json("database_snapshot_b.json", {k: v for k, v in snap_b.items() if k != "catalog"})
    schema_attestation = {
        "metadata_reattestation": metadata,
        "app_snapshot_a_identity": snap_a["identity"],
        "app_snapshot_b_identity": snap_b["identity"],
        "app_schema_catalog_sha256": snap_a["schema_catalog_sha256"],
        "app_schema_catalog_stable": snap_a["schema_catalog_sha256"] == snap_b["schema_catalog_sha256"],
        "alembic_revision_set": metadata.get("alembic_revision_set"),
        "schema_digest_algorithm_version": "p1_14e_r2_2_schema_catalog_digest_v1_full_catalog",
        "r2_vs_r2_1_digest_explanation": {
            "conclusion": "prior R2/R2.1 catalog digest prefixes must not be claimed equal because the summarization algorithms and included evidence changed; R2.2 recomputes and binds a fresh schema catalog digest",
            "r2_digest_prefix": "153240dc…",
            "r2_1_digest_prefix": "7add9617…",
            "algorithm_equality_claimed": False,
        },
    }
    write_json("schema_and_revision_attestation.json", schema_attestation)
    write_json("canonical_hash_spec.r2_2.json", {"version": "p1_14e_r2_2_canonical_hash_spec_v2", "rules": ["strings are never parsed as JSON", "only JSON/JSONB values are recursively normalized", "timestamps are converted to UTC RFC3339 with fixed microsecond precision", "UTF-8 bytes, ensure_ascii=false, compact separators, recursive key sort", "query_requests set order is created_at UTC then id", "set summary input structure is {'ids':[ordered ids],'hashes':{id:canonical_row_hash}}", "canonical implementation file sha256 is recorded"], "canonical_implementation_file": "p1_14e_r2_2.py", "canonical_implementation_sha256": sha_bytes(Path(__file__).read_bytes()), "query_requests_columns": snap_a["query_requests"]["columns"], "set_hash_algorithm": "SHA-256 over compact canonical JSON object containing ordered IDs and id-to-row-hash map"})
    write_json("query_requests_inventory_sanitized.json", snap_a["query_requests"])
    write_json("target_row_hashes.json", snap_a["query_requests"]["target_row_sha256"])
    write_json("legitimate_row_hashes.json", snap_a["query_requests"]["legitimate_row_sha256"])
    write_json("row_set_hashes.json", {"all_9_row_set_sha256": snap_a["query_requests"]["row_set_sha256"], "expected_all_9_row_set_sha256": EXPECTED_ALL_SET_SHA, "survivor_6_row_set_sha256": snap_a["query_requests"]["survivor_row_set_sha256"], "expected_survivor_6_row_set_sha256": EXPECTED_SURVIVOR_SET_SHA})
    write_json("dependency_scan_coverage.r2_2.json", snap_a["dependency"])
    write_json("dependency_closure_revalidation.r2_2.json", {"snapshot_a": snap_a["dependency"], "snapshot_b_sha256": snap_b["dependency"]["sha256"], "stable": snap_a["dependency"]["sha256"] == snap_b["dependency"]["sha256"], "coverage_complete": snap_a["dependency"]["coverage_complete"], "target_dependency_instances": snap_a["dependency"]["target_dependency_instances"], "target_dependency_instances_zero": snap_a["dependency"]["target_dependency_instances_zero"]})
    write_json("protected_chain_rowsets.r2_2.json", snap_a["protected"])
    write_json("trusted_object_rowsets.r2_2.json", snap_a["trusted"])
    expected = expected_deltas_r2_2(snap_a["query_requests"], snap_a["dependency"], snap_a["protected"], snap_a["trusted"])
    write_json("expected_deltas.r2_2.json", expected)
    sm = state_machine()
    sm["future_p1_14f_algorithm"]["recovery_mode"] = "TRANSACTION_ONLY"
    sm["future_p1_14f_algorithm"]["delete_sql_generated_in_r2_2"] = False
    write_json("transaction_state_machine.r2_2.json", sm)
    write_json("read_only_sql_log_sanitized.json", SQL_LOG)

    evidence_index = write_evidence_index()
    stable = (
        snap_a["query_requests"] == snap_b["query_requests"]
        and snap_a["dependency"]["sha256"] == snap_b["dependency"]["sha256"]
        and snap_a["protected"]["sha256"] == snap_b["protected"]["sha256"]
        and snap_a["trusted"]["sha256"] == snap_b["trusted"]["sha256"]
    )
    blockers = []
    if metadata.get("status") != "ATTESTED":
        blockers.append("P1_14E_R2_2_PENDING_PRIVILEGED_METADATA_REATTESTATION")
    if not evidence["all_required_hashes_valid"]:
        blockers.append("BLOCKED_SOURCE_EVIDENCE_HASH_MISMATCH")
    if not stable or not snap_a["query_requests"]["stable_against_r2"]:
        blockers.append("BLOCKED_REMEDIATION_TARGET_STATE_DRIFT")
    if snap_a["dependency"]["target_dependency_instances"]:
        blockers.append("BLOCKED_REMEDIATION_NONZERO_DEPENDENCY")
    if not snap_a["dependency"]["coverage_complete"]:
        blockers.append("BLOCKED_CONTAMINATION_DEPENDENCY_CLOSURE_INCOMPLETE")
    if any(snap_a["protected"]["intersection_with_targets"].values()) or not snap_a["protected"]["summary"]["preserved"]:
        blockers.append("BLOCKED_P1_14C_PROTECTED_CHAIN_OVERLAP")
    status = "P1_14E_R2_2_EXACT_REMEDIATION_PACKET_READY_AWAITING_USER_AUTHORIZATION" if not blockers else blockers[0]

    support_names = sorted([r["file"] for r in evidence_index["evidence_files"]] + ["evidence_index.r2_2.json", "evidence_index.r2_2.json.sha256"])
    support = [file_record(OUT / name) for name in support_names]
    manifest = {
        "schema_version": "p1_14e_r2_2_exact_remediation_authorization_packet.v1",
        "status": "PROPOSED_ONLY_NOT_EXECUTED",
        "authorized": False,
        "requires_user_authorization": True,
        "action_taken": "NONE",
        "delete_sql_generated": False,
        "authoritative_manifest": "remediation_manifest.r2_2.proposed.json",
        "source_r2_manifest_sha256": EXPECTED_R2_SHA,
        "defective_r2_1_manifest_sha256": EXPECTED_R2_1_SHA,
        "defective_r2_1_zip_sha256": EXPECTED_R2_1_ZIP_SHA,
        "supporting_files": support,
        "evidence_index_binding": file_record(OUT / "evidence_index.r2_2.json"),
        "database_identity": snap_a["identity"],
        "schema_catalog_sha256": snap_a["schema_catalog_sha256"],
        "canonical_hash_binding": {"spec_file": "canonical_hash_spec.r2_2.json", "target_row_hashes": snap_a["query_requests"]["target_row_sha256"], "legitimate_row_hashes": snap_a["query_requests"]["legitimate_row_sha256"], "all_9_row_set_sha256": snap_a["query_requests"]["row_set_sha256"], "survivor_6_row_set_sha256": snap_a["query_requests"]["survivor_row_set_sha256"]},
        "proposed_action": {"operation": "FUTURE_EXACT_PRIMARY_KEY_REMOVAL_REQUIRES_NEW_AUTHORIZATION", "target_schema": "epidemic", "target_table": "query_requests", "primary_key_column": "id", "target_count": 3, "executable_delete_sql_included": False},
        "target_rows": snap_a["query_requests"]["target_rows"],
        "protected_legitimate_rows": snap_a["query_requests"]["legitimate_rows"],
        "dependency_scan_sha256": snap_a["dependency"]["sha256"],
        "protected_chain_sha256": snap_a["protected"]["sha256"],
        "trusted_objects_sha256": snap_a["trusted"]["sha256"],
        "expected_deltas": expected,
        "transaction_state_machine": sm,
        "actual_database_writes": 0,
        "rows_deleted": 0,
        "review_decision_approval_promotion_trusted_writes": 0,
        "next_stage": "P1_14F_REQUIRES_NEW_EXACT_R2_2_USER_AUTHORIZATION",
    }
    write_json("remediation_manifest.r2_2.proposed.json", manifest)
    manifest_sha = write_sidecar("remediation_manifest.r2_2.proposed.json")

    report = {
        "status": status,
        "non_authorizing_display_document": True,
        "authoritative_manifest": "remediation_manifest.r2_2.proposed.json",
        "new_manifest_sha256": manifest_sha,
        "r2_manifest_sha256_matches": evidence["r2_manifest_matches"],
        "r2_1_manifest_sha256_matches_expected_defective_input": evidence["r2_1_manifest_matches"],
        "r2_1_zip_sha256_matches_expected_defective_input": evidence["r2_1_zip_matches"],
        "target_row_sha256": snap_a["query_requests"]["target_row_sha256"],
        "all_9_row_set_sha256": snap_a["query_requests"]["row_set_sha256"],
        "survivor_6_row_set_sha256": snap_a["query_requests"]["survivor_row_set_sha256"],
        "dependency_scan": {"coverage_complete": snap_a["dependency"]["coverage_complete"], "target_dependency_instances": snap_a["dependency"]["target_dependency_instances"], "sha256": snap_a["dependency"]["sha256"]},
        "protected_p1_14c": snap_a["protected"]["summary"],
        "trusted_objects_sha256": snap_a["trusted"]["sha256"],
        "actual_database_writes": 0,
        "rows_deleted": 0,
        "review_decision_approval_promotion_trusted_writes": 0,
        "blockers": blockers,
    }
    write_json("report.json", report)
    (OUT / "report.md").write_text("\n".join(["# P1-14E-R2.2 exact remediation authorization packet", "", "This is a non-authorizing display report. It does not authorize P1-14F.", "", f"Status: `{status}`", f"Authoritative manifest: `remediation_manifest.r2_2.proposed.json`", f"Manifest SHA-256: `{manifest_sha}`", f"All 9 query_requests row-set SHA-256: `{snap_a['query_requests']['row_set_sha256']}`", f"Six survivor row-set SHA-256: `{snap_a['query_requests']['survivor_row_set_sha256']}`", f"Dependency coverage complete: `{snap_a['dependency']['coverage_complete']}`", f"Target dependency instances: `{snap_a['dependency']['target_dependency_instances']}`", "Actual database writes=0; rows deleted=0; Review Decision/Approval/Promotion/Trusted writes=0."]) + "\n", encoding="utf-8")

    zip_members = sorted([
        p.name
        for p in OUT.iterdir()
        if p.is_file()
        and p.name not in {"package_index.r2_2.json", "package_index.r2_2.json.sha256", "complete_packet.r2_2.zip", "complete_packet.r2_2.zip.sha256"}
    ])
    package_index = build_package_index(zip_members)
    zip_sha, zip_verification = build_and_verify_zip(zip_members)
    if not zip_verification["verified"]:
        status = "BLOCKED_R2_2_PACKAGE_HASH_BINDING_MISMATCH"
        report["status"] = status
        report["blockers"] = [status]
        write_json("report.json", report)
    print(json.dumps({**report, "evidence_index_sha256": evidence_index["sha256"], "package_index_sha256": package_index["sha256"], "complete_packet_zip": str(OUT / "complete_packet.r2_2.zip"), "complete_packet_zip_sha256": zip_sha, "zip_verification": zip_verification}, ensure_ascii=False, indent=2, sort_keys=True))
'''
    p.write_text(s[:start] + new + s[end:], encoding="utf-8")


if __name__ == "__main__":
    main()
