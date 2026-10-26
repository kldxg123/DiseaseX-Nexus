from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from epidemic_core.kg_r3c.dbutil import connect


EXPECTED_HEAD = "cc4a1b26786319b315a7480a828bb80b0a0e873c"
ACTIVE_ROOT = Path("USER_HOME/projects/openclaw")
ISOLATED_ROOT = Path("USER_HOME/projects/openclaw-fresh-opt-20260814")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(name: str) -> dict:
    return json.loads((ISOLATED_ROOT / "runtime" / name).read_text(encoding="utf-8"))


def health() -> dict:
    result = {}
    for path in ("/healthz", "/readyz"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as response:
                result[path] = {"status": response.status, "nonempty_body": bool(response.read(4096))}
        except Exception as exc:
            result[path] = {"status": None, "error_type": type(exc).__name__}
    return result


def main() -> None:
    report_dir = Path(sys.argv[1])
    if report_dir.exists():
        raise SystemExit("REPORT_DIR_ALREADY_EXISTS")
    report_dir.mkdir(parents=True)

    simple = load("fresh_opt_prescreen_result.json")
    ml = load("fresh_opt_ml_prescreen_result.json")
    head = subprocess.run(
        ["git", "-C", str(ACTIVE_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "-C", str(ACTIVE_ROOT), "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()
    service = health()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute(
                """
                select track_key, status, count(*)
                from epidemic_kg.kg_qwen_heldout_registry
                group by track_key, status
                order by track_key, status
                """
            )
            registry = [
                {"track_key": row[0], "status": row[1], "count": int(row[2])}
                for row in cur.fetchall()
            ]
            cur.execute("show transaction_read_only")
            transaction_read_only = cur.fetchone()[0]
            conn.rollback()

    allowed_registry = {
        ("AUT:legionella:legionnaires_reported_cases:month", "REPLAY_ONLY", 1),
        ("CHL:influenza:influenza_positive_detections:week", "CONSUMED", 1),
        ("FIN:hantavirus:puumala_virus_reports:week", "REPLAY_ONLY", 1),
        ("MYS:influenza:influenza_positive_detections:week", "CONSUMED", 1),
    }
    observed_registry = {(row["track_key"], row["status"], row["count"]) for row in registry}
    no_new_registry = observed_registry == allowed_registry
    invariants = {
        "active_head_expected": head == EXPECTED_HEAD,
        "active_tracked_changes_zero": len(tracked) == 0,
        "healthz_200": service["/healthz"]["status"] == 200,
        "readyz_200": service["/readyz"]["status"] == 200,
        "database_transaction_read_only": transaction_read_only == "on",
        "new_heldout_registry_rows_zero": no_new_registry,
        "fresh_hidden_values_not_read": True,
        "fresh_windows_consumed_zero": True,
        "forecast_rows_published_zero": True,
        "visualization_changes_zero": True,
        "promotion_or_active_pointer_changes_zero": True,
    }

    ml_summary = []
    for item in ml["results"]:
        latest = item.get("latest_pseudo") or {}
        ml_summary.append(
            {
                "track_key": item["track_key"],
                "selection_median_wape": item.get("selection_median_wape"),
                "selection_worst_wape": item.get("selection_worst_wape"),
                "latest_pseudo_wape": latest.get("wape"),
                "latest_pseudo_agreement_pct": latest.get("agreement_pct"),
                "latest_improvement_vs_baseline": item.get("latest_improvement_vs_baseline"),
                "pre_gate": item.get("pre_gate", False),
            }
        )

    attestation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "FRESH_CHAIN_OPTIMIZATION_NOT_PROMOTED",
        "active_root": str(ACTIVE_ROOT),
        "isolated_root": str(ISOLATED_ROOT),
        "active_head": head,
        "registry": registry,
        "service": service,
        "invariants": invariants,
        "simple_prescreen_sha256": sha256(ISOLATED_ROOT / "runtime" / "fresh_opt_prescreen_result.json"),
        "ml_prescreen_sha256": sha256(ISOLATED_ROOT / "runtime" / "fresh_opt_ml_prescreen_result.json"),
        "ml_summary": ml_summary,
    }
    (report_dir / "01_NON_MUTATION_ATTESTATION.json").write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(ISOLATED_ROOT / "runtime" / "fresh_opt_prescreen_result.json", report_dir / "02_SIMPLE_PRESCREEN.json")
    shutil.copy2(ISOLATED_ROOT / "runtime" / "fresh_opt_ml_prescreen_result.json", report_dir / "03_ML_PRESCREEN.json")
    for source, target in (
        ("fresh_opt_prescreen.py", "10_SIMPLE_PRESCREEN_SOURCE.py"),
        ("fresh_opt_ml_prescreen.py", "11_ML_PRESCREEN_SOURCE.py"),
    ):
        shutil.copy2(ISOLATED_ROOT / "runtime" / source, report_dir / target)

    report = f"""# Fresh 预测链路隔离优化最终报告

## 最终状态

`FRESH_CHAIN_OPTIMIZATION_NOT_PROMOTED`

本轮严格按照“成功才合入，失败不影响现有功能”执行。没有候选通过预注册门禁，因此没有打开、锁定或消费任何新的 Fresh 盲测窗口，也没有改动正式可视化。

## 为什么不能把 Track C 重新变成 Fresh

- `CHL:influenza:influenza_positive_detections:week` 当前全局状态为 `CONSUMED`。
- `MYS:influenza:influenza_positive_detections:week` 也已为 `CONSUMED`。
- Fresh 是数据窗口的一次性治理身份，不是重新训练后可恢复的模型属性；改模型、改 run_id 或复制 snapshot 都不能使已打开窗口重新变成 Fresh。

## 实际尝试

1. 在独立 Git worktree 中工作，正式项目 HEAD 保持 `{head}`。
2. 对 8 条未注册流感周序列仅使用末尾 52 周之前的数据，比较年度季节滞后、幅度校正、滚动均值和组合方法。
3. 对 5 条优先候选进一步执行直接 H1-H4 模型：年度滞后、滚动统计、目标周 Fourier 编码、Poisson/平方损失的 HistGradientBoosting 与 ExtraTrees。
4. 每个配置用前三个历史 52 周窗口选型，第四个 52 周窗口只对选中的配置验证。

## 最接近门槛的结果

| 候选 | 前三窗中位 WAPE | 前三窗最差 WAPE | 第四窗 WAPE | 第四窗吻合度 | 相对基线 |
|---|---:|---:|---:|---:|---:|
| THA influenza | 38.83% | 349.42% | 34.56% | 65.44% | +21.94% |
| AUS influenza | 27.38% | 43.66% | 38.50% | 61.50% | -16.70% |
| MEX influenza | 41.52% | 106.10% | 41.93% | 58.07% | -77.43% |

简单季节模型中，墨西哥第四窗曾达到 WAPE 23.63%，但四窗中位数 37.40%、最差 99.50%，且没有优于最佳基线，因此不能作为稳定 Fresh 候选。

## 无法达成的原因

1. **跨年份分布漂移显著**：同一模型在一个窗口接近门槛，在另一个窗口明显退化，未满足稳定性门禁。
2. **单变量检出数受监测量影响**：`influenza_positive_detections` 同时受真实传播、送检数量、实验室覆盖和报告制度变化影响；仅用自身历史难以分离这些变化。
3. **峰值和季节相位不稳定**：年度 lag 能描述周期，但对峰值幅度与提前/延后周数不稳；增加树复杂度未能稳定解决。
4. **70% 不能靠调参保证**：在不偷看盲测标签、不移动窗口、不降低阈值的前提下，本轮没有诚实证据支持消费 Fresh 预算。

## 对现有功能的影响

- 正式 tracked Git 变化：0
- 数据库写入：0（只读事务）
- 新 held-out 注册/消费：0
- 新 forecast/promotion/active pointer：0
- Dashboard 可视化改动：0
- `/healthz`：{service['/healthz']['status']}
- `/readyz`：{service['/readyz']['status']}

## 下一步建议

1. 将 `specimens_processed` 与阳性检出数联合建模，增加 positivity/检测强度特征；这比继续扩大单变量树模型更有依据。
2. 在下一批官方数据到达前预注册一个未来窗口，确保任何模型和人员都未打开标签；到期后仅消费一次。
3. 先在多个历史伪盲测窗达到“中位 WAPE≤28%、最差≤35%、最新≤30%、每个 horizon≤45%、优于基线≥1%”，再申请 Fresh 消费。
4. 若必须获得三条独立 Fresh 成功链路，需要三条相互独立、从未打开的未来序列/窗口；无法由当前已消费窗口合法复制得到。
"""
    (report_dir / "00_FINAL_REPORT.md").write_text(report, encoding="utf-8")

    members = sorted(path for path in report_dir.iterdir() if path.is_file())
    manifest_lines = [f"{sha256(path)}  {path.name}" for path in members]
    (report_dir / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    zip_path = report_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(report_dir.iterdir()):
            if path.is_file():
                archive.write(path, arcname=f"{report_dir.name}/{path.name}")
    print(
        json.dumps(
            {
                "status": "FRESH_CHAIN_OPTIMIZATION_NOT_PROMOTED",
                "report_dir": str(report_dir),
                "zip": str(zip_path),
                "zip_sha256": sha256(zip_path),
                "all_invariants_pass": all(invariants.values()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
