from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

from PIL import Image


def members(path: Path):
    out = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("word/media/"):
                continue
            data = archive.read(name)
            try:
                image = Image.open(io.BytesIO(data))
                size = image.size
                fmt = image.format
            except Exception:
                size = None
                fmt = None
            out[name] = (hashlib.sha256(data).hexdigest(), len(data), size, fmt)
    return out


left = members(Path(sys.argv[1]))
right = members(Path(sys.argv[2]))
for name in sorted(set(left) | set(right)):
    if left.get(name) != right.get(name):
        print(name)
        print("  LEFT ", left.get(name))
        print("  RIGHT", right.get(name))
