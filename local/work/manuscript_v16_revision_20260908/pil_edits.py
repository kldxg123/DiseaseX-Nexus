from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

STAGE = Path(__file__).resolve().parent
MEDIA = Path(r"F:\作业\aibola\.codex_tmp\v15_media")

# ---- Fig 6 -> Fig 4: retitle image12 ----
img = Image.open(MEDIA / "image12.png").convert("RGB")
draw = ImageDraw.Draw(img)
w, h = img.size
# sample title colour from an existing title glyph pixel row
col = img.getpixel((w // 2, 62))
# find a dark pixel near the title for colour
title_col = (23, 32, 51)  # #172033 used by generator (revise_submission_v6.py)
draw.rectangle((0, 0, w, 135), fill=(255, 255, 255))
f = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 56)
draw.text((w // 2, 62), "Figure 4 | Retrospective evaluation and ablation", font=f, fill=title_col, anchor="ma")
img.save(STAGE / "image12_fig4.png", optimize=True)
print("fig4 title done", img.size)

# ---- Fig 1: replace 'Bayesian Optimization/ RL' with 'Allowlisted parameter tuning' ----
img = Image.open(MEDIA / "image1.png").convert("RGB")
draw = ImageDraw.Draw(img)
bg = img.getpixel((1090, 400))
print("sampled bg:", bg)
draw.rectangle((795, 372, 1112, 478), fill=bg)
f1 = ImageFont.truetype(r"C:\Windows\Fonts\times.ttf", 40)
cx = 953
draw.text((cx, 400), "Allowlisted", font=f1, fill=(0, 0, 0), anchor="ma")
draw.text((cx, 446), "parameter tuning", font=f1, fill=(0, 0, 0), anchor="ma")
img.save(STAGE / "image1_fixed.png", optimize=True)
print("fig1 done", img.size)
