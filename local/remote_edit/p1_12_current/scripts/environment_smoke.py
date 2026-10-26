from __future__ import annotations
import os, platform, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
required = ["apps", "packages", "config", "docs", "scripts", "tests", "data", "models", "storage", "reports"]
checks = {
    "python_3_10": sys.version_info[:2] == (3, 10),
    "required_directories": all((ROOT / item).is_dir() for item in required),
    "data_disk_writable": os.access("USER_HOME/autodl-tmp", os.W_OK),
}
print(f"python={platform.python_version()}")
print(f"executable={sys.executable}")
print(f"os={platform.platform()}")
print(f"cwd={Path.cwd()}")
for name, ok in checks.items(): print(f"{name}={'PASS' if ok else 'FAIL'}")
raise SystemExit(0 if all(checks.values()) else 1)
