#!/usr/bin/env python3
import json, sys, urllib.request, urllib.error, urllib.parse, http.cookiejar
creds = sys.stdin.read().splitlines()
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
r = urllib.request.Request("http://127.0.0.1:8020/login",
                           data=urllib.parse.urlencode({"username": creds[0].strip(), "password": creds[1].strip()}).encode(),
                           method="POST")
opener.open(r, timeout=30)
for url in ("/api/v1/kg/r3c/recommendations?eligible_only=true&limit=5",
            "/api/v1/kg/r3c/recommendations?limit=5"):
    with opener.open("http://127.0.0.1:8020" + url, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    print("URL:", url)
    print(json.dumps(body, ensure_ascii=False)[:1200])
    print("---")
