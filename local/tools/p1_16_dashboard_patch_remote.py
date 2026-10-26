from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_historical_and_real_influenza_region_week_pipeline"
APP = PROJECT / "src/epidemic_core/dashboard/app.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    before = sha(APP)
    text = APP.read_text(encoding="utf-8")
    text = text.replace(
        '<h2 id="prediction-demo-title">Outbreak Onset Risk Prediction����ʾ��</h2>',
        '<h2 id="prediction-demo-title">Operational Influenza Activity Onset Risk（真实 FluNet 回顾性演示）</h2>',
    )
    text = text.replace(
        '<p class="preview-warning">DEMO ONLY / NOT FOR PUBLIC-HEALTH DECISIONS����ʵ Trusted ���ݵ�ǰ������ѵ������֤�� onset ģ�ͣ�������ֻչʾ SAMPLE_DATA / PIPELINE_SMOKE_TEST ������·��</p>',
        '<p class="preview-warning">REAL OFFICIAL DATA / RETROSPECTIVE ENGINEERING DEMO / NOT A VALIDATED PUBLIC-HEALTH WARNING SYSTEM / DOES NOT VALIDATE EBOLA PREDICTION。</p>',
    )
    if '/static/dashboard.prediction-demo.js" defer' not in text:
        text = text.replace("</body>", '<script src="/static/dashboard.prediction-demo.js" defer></script></body>', 1)
    old = 'payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_data_visualization_prediction_engineering_loop" / "prediction_dashboard_demo_payload.json"'
    new = (
        'payload_path = Path(__file__).resolve().parents[3] / "reports" / '
        '"ebola_historical_and_real_influenza_region_week_pipeline" / "prediction_dashboard_payload.json"\n'
        '        if not payload_path.exists():\n'
        '            payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_data_visualization_prediction_engineering_loop" / "prediction_dashboard_demo_payload.json"'
    )
    if old in text:
        text = text.replace(old, new, 1)
    text = text.replace(
        'payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_historical_and_real_influenza_region_week_pipeline" / "prediction_dashboard_payload.json"\\n        if not payload_path.exists():\\n            payload_path = Path(__file__).resolve().parents[3] / "reports" / "ebola_data_visualization_prediction_engineering_loop" / "prediction_dashboard_demo_payload.json"',
        new,
    )
    APP.write_text(text, encoding="utf-8")
    after = sha(APP)
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "dashboard_patch_report.json").write_text(
        json.dumps(
            {
                "status": "P1_16_DASHBOARD_PATCH_APPLIED",
                "path": str(APP.relative_to(PROJECT)),
                "before_sha256": before,
                "after_sha256": after,
                "changed": before != after,
                "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "P1_16_DASHBOARD_PATCH_APPLIED", "changed": before != after}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
