from pathlib import Path

from PIL import Image


src = Path(r"F:\作业\aibola\qa\manuscript_v14_highlighted_placeholders_render")
dst = src / "review_jpg"
dst.mkdir(exist_ok=True)
pages = sorted(src.glob("page-*.png"))
for page in pages:
    with Image.open(page) as image:
        image.convert("RGB").save(dst / f"{page.stem}.jpg", quality=72, optimize=True)
print(len(pages))
print(dst)
