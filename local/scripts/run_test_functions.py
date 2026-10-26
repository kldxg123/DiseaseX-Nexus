from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path


def main() -> int:
    sys.path.insert(0, str(Path(".").resolve()))
    failures = []
    for path in sorted(Path("tests").glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("test_"):
                try:
                    fn()
                    print(f"PASS {path.name}::{name}")
                except Exception as exc:
                    failures.append((path.name, name, exc))
                    print(f"FAIL {path.name}::{name}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
