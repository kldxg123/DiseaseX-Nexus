import importlib.util
import sys


print(f"python={sys.version_info.major}.{sys.version_info.minor}")
for name in ("numpy", "pandas", "sklearn", "xgboost", "psycopg"):
    print(f"{name}={importlib.util.find_spec(name) is not None}")
