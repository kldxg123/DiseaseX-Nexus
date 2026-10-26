from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(r"F:\作业\aibola\reports\nc_manuscript_comparison_alignment_20260823\qa")
for folder in (ROOT / "main", ROOT / "supplementary"):
    pages = sorted(folder.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[1]))
    for start in range(0, len(pages), 4):
        batch = pages[start : start + 4]
        sheet = Image.new("RGB", (1640, 2200), "#d9dde3")
        for i, page in enumerate(batch):
            im = Image.open(page).convert("RGB")
            im.thumbnail((800, 1050), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (820, 1100), "white")
            canvas.paste(im, ((820 - im.width) // 2, 35))
            ImageDraw.Draw(canvas).text((15, 8), page.stem, fill="black")
            sheet.paste(canvas, ((i % 2) * 820, (i // 2) * 1100))
        sheet.save(folder / f"contact-{start+1:02d}-{start+len(batch):02d}.jpg", quality=82, optimize=True)
print("done")
