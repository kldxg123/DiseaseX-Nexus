from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def page_number(path: Path) -> int:
    return int(path.stem.split("-")[-1])


def main() -> None:
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    output.mkdir(parents=True, exist_ok=True)
    pages = sorted(source.glob("page-*.png"), key=page_number)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 24)
    for start in range(0, len(pages), 2):
        items: list[tuple[Path, Image.Image]] = []
        for path in pages[start : start + 2]:
            image = Image.open(path).convert("RGB")
            image.thumbnail((1200, 1600))
            items.append((path, image.copy()))
        width = sum(image.width for _, image in items) + 60 * (len(items) + 1)
        height = max(image.height for _, image in items) + 100
        sheet = Image.new("RGB", (width, height), "#D9DEE6")
        draw = ImageDraw.Draw(sheet)
        x = 60
        for path, image in items:
            draw.text((x, 20), path.stem, fill="black", font=font)
            sheet.paste(image, (x, 60))
            x += image.width + 60
        output_path = output / f"pages-{start + 1:02d}-{start + len(items):02d}.jpg"
        sheet.save(output_path, "JPEG", quality=76, optimize=True)
        print(output_path)


if __name__ == "__main__":
    main()
