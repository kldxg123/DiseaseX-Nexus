#!/usr/bin/env python3
"""Parallel ranged downloader for ModelScope model files (resume-capable)."""
import concurrent.futures as cf
import os
import sys
import time
import urllib.request

BASE = "https://modelscope.cn/models/Qwen/Qwen3-4B-Instruct-2507/resolve/master/"
DEST = "USER_HOME/models/Qwen3-4B-Instruct-2507"
STREAMS = 12
CHUNK = 1 << 20

FILES = [
    ("config.json", 727),
    ("configuration.json", 73),
    ("generation_config.json", 238),
    ("LICENSE", 11343),
    ("merges.txt", 1671839),
    ("model-00001-of-00003.safetensors", 3957900840),
    ("model-00002-of-00003.safetensors", 3987450520),
    ("model-00003-of-00003.safetensors", 99630640),
    ("model.safetensors.index.json", 32819),
    ("README.md", 8168),
    ("tokenizer.json", 11422654),
    ("tokenizer_config.json", 9377),
    ("vocab.json", 2776833),
    (".gitattributes", 2221),
]


def fetch_range(url: str, start: int, end: int, out: str) -> int:
    if os.path.exists(out) and os.path.getsize(out) == end - start + 1:
        return 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    got = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(out, "wb") as f:
        while True:
            buf = r.read(CHUNK)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
    if got != end - start + 1:
        raise IOError(f"short read {out}: {got} != {end - start + 1}")
    return got


def download_file(name: str, size: int) -> None:
    url = BASE + name
    dest = os.path.join(DEST, name)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        print(f"SKIP {name}", flush=True)
        return
    if size < 8 << 20:
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
                    f.write(r.read())
                break
            except Exception as e:
                print(f"retry {name}: {e}", flush=True)
                time.sleep(3)
        print(f"DONE {name}", flush=True)
        return
    bounds = []
    step = size // STREAMS + 1
    for i in range(STREAMS):
        s = i * step
        e = min(size - 1, (i + 1) * step - 1)
        if s <= e:
            bounds.append((s, e, f"{dest}.part{i}"))
    for attempt in range(8):
        try:
            with cf.ThreadPoolExecutor(max_workers=STREAMS) as ex:
                list(ex.map(lambda b: fetch_range(url, b[0], b[1], b[2]), bounds))
            break
        except Exception as e:
            print(f"retry {name}: {e}", flush=True)
            time.sleep(5)
    else:
        raise SystemExit(f"FAILED {name}")
    with open(dest, "wb") as out:
        for _, _, part in bounds:
            with open(part, "rb") as p:
                while True:
                    buf = p.read(CHUNK)
                    if not buf:
                        break
                    out.write(buf)
    for _, _, part in bounds:
        os.unlink(part)
    assert os.path.getsize(dest) == size, f"size mismatch {name}"
    print(f"DONE {name} {size}", flush=True)


def main() -> None:
    os.makedirs(DEST, exist_ok=True)
    t0 = time.time()
    for name, size in FILES:
        download_file(name, size)
    print(f"ALL_DONE in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
