import urllib.request

for name in ("healthz", "readyz"):
    with urllib.request.urlopen(f"http://127.0.0.1:8020/{name}", timeout=8) as response:
        print(f"{name.upper()}={response.status}")
