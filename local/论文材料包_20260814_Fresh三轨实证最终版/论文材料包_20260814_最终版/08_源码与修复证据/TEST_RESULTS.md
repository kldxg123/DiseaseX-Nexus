# 测试结果

- pytest（知识图谱 + Qwen API）：22 passed，34 条 Starlette cookie deprecation warnings。
- Ruff：PASS。
- Python py_compile：PASS。
- JavaScript syntax（bundled Node）：PASS。
- git diff --check：PASS。
- secret scan：PASS。
- TLS bypass scan：PASS。
- healthz/readyz：200/200。
- Strict Mypy：NOT_RUN_ENVIRONMENT_MISSING_LIBRT_INTERNAL。
- Git commit：cc4a1b2（未 push）。
