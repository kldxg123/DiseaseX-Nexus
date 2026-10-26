#!/usr/bin/env python3
"""Validate browser PNGs and build a thumbnail-only contact sheet."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat


PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect(path: Path, root: Path) -> dict[str, object]:
    raw_magic = path.read_bytes()[:8]
    result: dict[str, object] = {
        "relative_path": path.relative_to(root).as_posix(),
        "file_size": path.stat().st_size,
        "magic_ok": raw_magic == PNG_MAGIC,
        "mime": "image/png" if raw_magic == PNG_MAGIC else "unknown",
        "sha256": sha256(path),
    }
    try:
        with Image.open(path) as image:
            image.load()
            rgb = image.convert("RGB")
            extrema = ImageStat.Stat(rgb).extrema
            corner = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
            bbox = ImageChops.difference(rgb, corner).getbbox()
            colors = rgb.resize((min(320, rgb.width), min(200, rgb.height))).getcolors(
                maxcolors=320 * 200
            )
            result.update(
                {
                    "decode_ok": True,
                    "width": rgb.width,
                    "height": rgb.height,
                    "mode": image.mode,
                    "content_bbox": list(bbox) if bbox else None,
                    "channel_extrema": [list(pair) for pair in extrema],
                    "sampled_color_count": len(colors) if colors else None,
                    "blank_or_monochrome": bbox is None or all(lo == hi for lo, hi in extrema),
                }
            )
    except Exception as exc:  # pragma: no cover - evidence path
        result.update(
            {
                "decode_ok": False,
                "decode_error": type(exc).__name__,
                "blank_or_monochrome": True,
            }
        )
    return result


def make_contact_sheet(root: Path, entries: list[dict[str, object]]) -> None:
    core = [e for e in entries if str(e["relative_path"]).startswith("core_figures/")]
    if not core:
        core = entries
    cols = 3
    cell_w, cell_h = 520, 350
    rows = math.ceil(len(core) / cols)
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for idx, entry in enumerate(core):
        x = (idx % cols) * cell_w
        y = (idx // cols) * cell_h
        path = root / str(entry["relative_path"])
        with Image.open(path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((cell_w - 24, cell_h - 54), Image.Resampling.LANCZOS)
            px = x + (cell_w - thumb.width) // 2
            py = y + 34 + (cell_h - 44 - thumb.height) // 2
            canvas.paste(thumb, (px, py))
        label = Path(str(entry["relative_path"])).stem[:70]
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="#9ca3af", width=1)
        draw.text((x + 10, y + 10), label, fill="#0f172a", font=font)
    canvas.save(root / "CONTACT_SHEET.png", format="PNG")


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    files = sorted(
        p
        for p in root.rglob("*.png")
        if p.name != "CONTACT_SHEET.png" and p.is_file()
    )
    entries = [inspect(p, root) for p in files]
    sha_groups: dict[str, list[str]] = {}
    for entry in entries:
        sha_groups.setdefault(str(entry["sha256"]), []).append(str(entry["relative_path"]))
    duplicate_groups = [paths for paths in sha_groups.values() if len(paths) > 1]
    summary = {
        "png_count_before_contact_sheet": len(entries),
        "decode_failure_count": sum(not bool(e.get("decode_ok")) for e in entries),
        "blank_or_monochrome_count": sum(bool(e.get("blank_or_monochrome")) for e in entries),
        "unexpected_duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "entries": entries,
    }
    machine = root / "machine"
    machine.mkdir(exist_ok=True)
    (machine / "png_qa.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    make_contact_sheet(root, entries)
    contact = inspect(root / "CONTACT_SHEET.png", root)
    (machine / "contact_sheet_qa.json").write_text(
        json.dumps(contact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "entries"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
