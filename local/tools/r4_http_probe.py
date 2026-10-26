#!/usr/bin/env python3
from urllib.request import urlopen
from urllib.error import HTTPError
for p in ["/healthz", "/readyz", "/api/v1/forecast-model-kg/r4/model-registry"]:
    try:
        with urlopen("http://127.0.0.1:8020" + p, timeout=5) as r:
            print(p, r.status)
    except HTTPError as e:
        print(p, e.code)
    except Exception as e:
        print(p, type(e).__name__)
