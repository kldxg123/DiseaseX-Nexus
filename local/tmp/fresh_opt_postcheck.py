import json
import urllib.request


result = {}
for path in ("/healthz", "/readyz"):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as response:
            body = response.read(4096)
            result[path] = {
                "status": response.status,
                "content_type": response.headers.get("Content-Type"),
                "nonempty_body": bool(body),
            }
    except Exception as exc:
        result[path] = {"status": None, "error_type": type(exc).__name__}
print(json.dumps(result, ensure_ascii=False, indent=2))
