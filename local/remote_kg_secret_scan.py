from __future__ import annotations

from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
REPORT = ROOT / "reports/forecast_model_kg_v1_20260729T053519Z_nogit"
PATHS = [
    ROOT / "reference/forecast_model_knowledge_graph.v1.json",
    ROOT / "reference/forecast_model_knowledge_graph.schema.json",
    ROOT / "docs/forecast_model_knowledge_graph.md",
    ROOT / "src/epidemic_core/forecast_model_kg",
    ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js",
    ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.css",
    ROOT / "tests/unit/test_forecast_model_kg.py",
]
PATTERNS = [
    "PRIVATE KEY",
    "PASSWORD",
    "TOKEN",
    "SECRET",
    "http_proxy",
    "https_proxy",
    "GIT_SSL_NO_VERIFY",
    "--trusted-host",
    "curl -k",
    "no-check-certificate",
]


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in PATHS:
        if path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file())
        elif path.exists():
            files.append(path)
    return files


def main() -> None:
    findings: list[str] = []
    for path in iter_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            upper = line.upper()
            for pattern in PATTERNS:
                if pattern.upper() in upper:
                    findings.append(f"{path}:{lineno}:{pattern}")
    output = REPORT / "SECRET_AND_TLS_BYPASS_SCAN.txt"
    if findings:
        output.write_text("\n".join(findings) + "\n", encoding="utf-8")
        print("SCAN_NONEMPTY")
        raise SystemExit(1)
    output.write_text("PASS: no secret markers or TLS-bypass markers in stage-scoped files.\n", encoding="utf-8")
    print("SCAN_PASS")


if __name__ == "__main__":
    main()
