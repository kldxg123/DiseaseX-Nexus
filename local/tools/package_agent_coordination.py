"""Build a focused, sanitized source reference bundle; never contact remote hosts."""
from pathlib import Path
import ast
import hashlib
import json
import zipfile
from package_local_snapshot import sanitize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliveries" / "Agent_Coordination_20261023"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = []
    # Dependency support is included; KG recommendations and forecast-model ranking are not.
    for directory in (ROOT / "packages").iterdir():
        if not directory.is_dir() or directory.name in {"__pycache__", "model_benchmark_agent"}:
            continue
        files.extend(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".json", ".yaml", ".yml"})
    test_names = ["test_worker_job_registry.py", "test_worker_scheduler.py", "test_worker_tasks.py", "test_worker_job_runner.py", "test_worker_pipeline.py", "test_predictor_baselines.py", "test_prediction_store.py", "test_deepseek_table_query_orchestrator.py", "test_deepseek_table_review_worker.py"]
    files.extend(ROOT / "tests" / name for name in test_names)
    files.extend([ROOT / "requirements.txt", ROOT / "pyproject.toml"])
    docs = OUT / "智能体多模型协调说明.txt"
    manifest = []
    changed = []
    invalid = []
    archive = OUT / "DiseaseX_Agent_Coordination_Code_Docs_20261023.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(set(files)):
            if not path.exists():
                continue
            rel = path.relative_to(ROOT).as_posix()
            if any(word in path.name.lower() for word in ("credential", "secret", "model_governance")):
                continue
            text = path.read_text(encoding="utf-8-sig")
            text, redacted = sanitize(text)
            if path.suffix == ".py":
                try:
                    ast.parse(text)
                except SyntaxError:
                    invalid.append(rel)
                    continue
            payload = text.encode("utf-8")
            z.writestr("code/" + rel, payload)
            manifest.append({"path": "code/" + rel, "sha256": hashlib.sha256(payload).hexdigest(), "redacted": redacted})
            if redacted:
                changed.append(rel)
        z.writestr("未收入的语法异常文件.json", json.dumps(invalid, ensure_ascii=False, indent=2))
        z.writestr("脱敏校验补充说明.txt", f"有{len(invalid)}个支持文件的原文或脱敏副本未通过语法校验，已排除，具体路径见未收入的语法异常文件.json。所收录Python副本全部通过语法检查。配置及支持文件的缺失也意味着本包不具备完整独立运行依赖。未运行功能测试，不应将语法和归档校验理解为端到端功能验证。")
        z.write(docs, "智能体多模型协调说明.txt")
        z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("验证与范围说明.txt", "本包为2026-10-23当前本地代码的专题参考摘包，不是完整部署包。\n所有收入Python文件均通过AST语法检查；压缩包通过CRC和逐文件SHA256验证。未执行网络调用或模型训练。\n测试文件作为现有行为的参考保留；完整运行需要原项目测试conftest、fixtures、API模块等环境依赖，不声明本摘包可独立通过全部测试。\n不包含预测模型六维评分、LLM选模、知识图谱推荐和模型基准筛选模块；底层按调用方提供model_name取得执行器的接口予以保留，它不负责评价或决定哪个模型最佳。\nmodel_governance模块未收录；若包内其他模块引用该接口，需使用原项目环境。本包不是依赖闭包。\n凭据、服务器信息、实际环境变量、私钥和运行数据均未收录。文本中的检测到的敏感字面量使用占位符替换，原始代码未修改。依赖目录中的来源质量评分不属于预测模型选型，阅读主线以中文说明中的文件索引为准。\n")
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for item in manifest:
            assert hashlib.sha256(z.read(item["path"])).hexdigest() == item["sha256"]
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (OUT / "SHA256.txt").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"files": len(manifest), "redacted": len(changed), "python_syntax_checked": sum(item["path"].endswith(".py") for item in manifest), "excluded_syntax_files": len(invalid), "zip_bytes": archive.stat().st_size, "crc_and_hash": "passed"}))


if __name__ == "__main__":
    main()
