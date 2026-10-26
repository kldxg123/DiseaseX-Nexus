from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing patch anchor: {old[:120]}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: install_kg_p1_r3r.py <upload_dir>")
    upload = Path(sys.argv[1])
    utc = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    project = Path("USER_HOME/projects/openclaw")
    report = Path(f"/home/dell/reports/kg_p1_r3r_paper_claim_recommendation_{utc}")
    backup = Path(f"/home/dell/backups/openclaw_kg_p1_r3r_{utc}")
    work = Path(f"/home/dell/work/kg_p1_r3r_paper_claim_recommendation_{utc}")
    for d in (report, backup, work):
        d.mkdir(parents=True, exist_ok=False)

    files = [
        project / "src/epidemic_core/forecast_model_kg/service.py",
        project / "src/epidemic_core/forecast_model_kg/dashboard_app.py",
        project / "src/epidemic_core/dashboard/static/forecast-model-kg.js",
        project / "src/epidemic_core/dashboard/static/forecast-model-kg.css",
    ]
    manifest: dict[str, object] = {"utc": utc, "project": str(project), "files": {}}
    for f in files:
        rel = f.relative_to(project)
        dst = backup / "source" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        manifest["files"][str(rel)] = {"sha256": sha(f), "bytes": f.stat().st_size}

    pg_dump = project / "runtime/postgres18x/bin/pg_dump"
    pg_restore = project / "runtime/postgres18x/bin/pg_restore"
    dump = backup / "epidemic_openclaw_pre_r3r.dump"
    subprocess.run(
        [
            str(pg_dump),
            "-h",
            str(project / "runtime/postgres-run"),
            "-p",
            "55432",
            "-U",
            "postgres",
            "-d",
            "epidemic_openclaw",
            "-Fc",
            "-f",
            str(dump),
        ],
        check=True,
    )
    restore_list = subprocess.check_output([str(pg_restore), "--list", str(dump)], text=True)
    (backup / "pg_restore_list.txt").write_text(restore_list, encoding="utf-8")
    manifest["db_backup"] = {"path": str(dump), "sha256": sha(dump), "pg_restore_list_lines": len(restore_list.splitlines())}
    (report / "R3R_PRECHANGE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Install R3R module.
    module_dst = project / "src/epidemic_core/forecast_model_kg/r3r.py"
    shutil.copy2(upload / "kg_p1_r3r_module.py", module_dst)

    # Patch service.py to call R3R augmentation.
    service = project / "src/epidemic_core/forecast_model_kg/service.py"
    text = service.read_text(encoding="utf-8")
    if "from .r3r import augment_guidance_response" not in text:
        text = text.replace("from .loader import load_graph, validate_graph, disease_alias_map\n", "from .loader import load_graph, validate_graph, disease_alias_map\nfrom .r3r import augment_guidance_response\n", 1)
    text = replace_once(
        text,
        "def guidance_response(engine: Engine | None, disease: str, country: str | None, target: str | None, horizon: int | None, explicit_profile: dict[str, Any] | None = None) -> dict[str, Any]:",
        'def guidance_response(engine: Engine | None, disease: str, country: str | None, target: str | None, horizon: int | None, explicit_profile: dict[str, Any] | None = None, frequency: str | None = "week") -> dict[str, Any]:',
    )
    text = replace_once(
        text,
        '    result["dataset_profile_source"] = source\n    return result\n',
        '    result["dataset_profile_source"] = source\n    return augment_guidance_response(engine, result, disease=disease, country=country_norm, target=target, horizon=horizon, frequency=frequency)\n',
    )
    service.write_text(text, encoding="utf-8")

    # Patch route signature, HTML frequency input, and pass-through.
    dash = project / "src/epidemic_core/forecast_model_kg/dashboard_app.py"
    dtext = dash.read_text(encoding="utf-8")
    if 'id="kgFrequency"' not in dtext:
        dtext = replace_once(
            dtext,
            '<label>&#30446;&#26631; / Target<input id="kgTarget" value="cases_new"></label>\n      <label>Horizon weeks',
            '<label>&#30446;&#26631; / Target<input id="kgTarget" value="cases_new"></label>\n      <label>&#39057;&#29575; / Frequency<input id="kgFrequency" value="week"></label>\n      <label>Horizon weeks',
        )
    dtext = replace_once(
        dtext,
        "        horizon: int | None = 4,\n        demo_profile: str | None = None,",
        '        horizon: int | None = 4,\n        frequency: str | None = "week",\n        demo_profile: str | None = None,',
    )
    dtext = replace_once(
        dtext,
        "guidance_response(engine, disease, country_or_region, target, horizon, explicit_profile=explicit_profile)",
        "guidance_response(engine, disease, country_or_region, target, horizon, explicit_profile=explicit_profile, frequency=frequency)",
    )
    dash.write_text(dtext, encoding="utf-8")

    shutil.copy2(upload / "forecast-model-kg.r3r.js", project / "src/epidemic_core/dashboard/static/forecast-model-kg.js")
    css = project / "src/epidemic_core/dashboard/static/forecast-model-kg.css"
    ctext = css.read_text(encoding="utf-8")
    if ".kg-paper" not in ctext:
        ctext += "\n.kg-paper{border:1px solid #c7d8ef;border-radius:12px;padding:.8rem;margin:.6rem 0;background:#fbfdff}.kg-table{width:100%;border-collapse:collapse}.kg-table td,.kg-table th{border-bottom:1px solid #dbe5f3;padding:.45rem;text-align:left;vertical-align:top}.kg-pill{display:inline-block;background:#e7f1ff;color:#17437a;border-radius:999px;padding:.16rem .5rem;margin:.1rem}.kg-card{border:1px solid #d8e2ef;border-radius:12px;padding:.8rem;margin:.6rem 0;background:#fff}.kg-edge{stroke:#54709b;stroke-width:2}.kg-node{fill:#eaf3ff;stroke:#1c5aa6;stroke-width:2}.kg-label{font-size:10px;fill:#10213f}pre{white-space:pre-wrap;background:#f5f7fb;border:1px solid #d8e2ef;border-radius:10px;padding:.7rem;max-height:280px;overflow:auto}\n"
        css.write_text(ctext, encoding="utf-8")

    post = {"utc": utc, "files": {}}
    for f in files + [module_dst]:
        rel = f.relative_to(project)
        pre = manifest["files"].get(str(rel), {}) if isinstance(manifest["files"], dict) else {}
        post["files"][str(rel)] = {"sha256": sha(f), "bytes": f.stat().st_size, "pre_sha256": pre.get("sha256")}
    (report / "KG_P1_R3R_SOURCE_MANIFEST.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    diff_text = ""
    for f in files:
        rel = f.relative_to(project)
        old = backup / "source" / rel
        diff = subprocess.run(["diff", "-u", str(old), str(f)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        diff_text += diff.stdout + "\n"
    (report / "KG_P1_R3R_SOURCE_DIFF.patch").write_text(diff_text, encoding="utf-8")
    print(json.dumps({"utc": utc, "work": str(work), "report": str(report), "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
