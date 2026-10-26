from __future__ import annotations

import csv
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from docx import Document
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(r"F:\作业\aibola\reports\nc_submission_revision_20260824\NC_Submission_Updated_20260824")
SI = ROOT / "Supplementary"
SI_FIG = SI / "SI_Figures"
BENCH = ROOT / "Benchmark_Evidence"
EXP = SI / "Table4_Figure6_Experiment_20260824"
ARCHIVE = ROOT / "Archive_Previous_20260824_Pre_Page21_CodeFigure_Revision"

MAIN_IN = ROOT / "DiseaseX_Nexus_NC_Manuscript_v5_20260824.docx"
MAIN_OUT = ROOT / "DiseaseX_Nexus_NC_Manuscript_v6_20260824.docx"
SI_IN = SI / "Supplementary_Information_Updated_v2_20260824.docx"
SI_OUT = SI / "Supplementary_Information_Updated_v3_20260824.docx"
MANIFEST_IN = ROOT / "Content_Manifest_Updated_v2_20260824.docx"
MANIFEST_OUT = ROOT / "Content_Manifest_Updated_v3_20260824.docx"

CURRENT_FIG1 = SI_FIG / "Main_Figure_1_System_Architecture.jpeg"
CURRENT_FIG3 = ROOT / "Figures" / "figure3.jpg"
CURRENT_SI_FIG3 = SI_FIG / "Main_Figure_3_Brazil_Dengue.jpeg"
CURRENT_FIG6 = ROOT / "Figures" / "Figure_6_Retrospective_Multi_Pathogen_Evaluation.png"

ORIGINAL_FIG1 = ARCHIVE / "Main_Figure_1_System_Architecture.jpeg"
ORIGINAL_FIG3_MAIN = ARCHIVE / "figure3.jpg"
ORIGINAL_FIG3_SI = ARCHIVE / "Main_Figure_3_Brazil_Dengue.jpeg"

NEW_FIG6 = EXP / "Figure_6_Evaluation_and_Ablation.png"
NEW_FIG6_MAIN = ROOT / "Figures" / "Figure_6_Evaluation_and_Ablation.png"
NEW_FIG6_SI = SI_FIG / "Main_Figure_6_Evaluation_and_Ablation.png"

SUPP_FIGS = [
    SI_FIG / "Supplementary_Figure_1_HK_Flu_Main_RMSE.png",
    SI_FIG / "Supplementary_Figure_2_HK_Flu_Horizon_RMSE.png",
    SI_FIG / "Supplementary_Figure_3_HK_Flu_PostCOVID_RMSE.png",
    SI_FIG / "Supplementary_Figure_4_Governed_Tracks.png",
]

FONT_DIR = Path(r"C:\Windows\Fonts")
TEXT = "#172033"
NAVY = "#17365d"
BLUE = "#2f6fb0"
GREY = "#94a3b8"
ORANGE = "#ef982d"
PURPLE = "#7c3aed"
GREEN = "#3aa978"
RED = "#b42318"
GRID = "#dbe2ea"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / ("arialbd.ttf" if bold else "arial.ttf")), size)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(draw: ImageDraw.ImageDraw, xy, value: str, size=34, fill=TEXT, bold=False, anchor=None):
    draw.text(xy, value, font=font(size, bold), fill=fill, anchor=anchor)


def save_png(image: Image.Image, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def generate_supplementary_figures() -> None:
    overall = {row["model"]: row for row in read_csv(BENCH / "test_metrics_overall.csv")}
    horizons = read_csv(BENCH / "test_metrics_by_horizon.csv")
    post = read_csv(BENCH / "postcovid_metrics.csv")

    # Supplementary Figure 1: overall paired RMSE.
    labels = ["Paper constant baseline", "DiseaseX adaptive ensemble", "Paper AWBE", "Horizon-aware router"]
    values = [
        float(overall["Baseline"]["rmse"]),
        float(overall["KG_ADAPTIVE_ENSEMBLE"]["rmse"]),
        float(overall["AWBE"]["rmse"]),
        2.7255007802909184,
    ]
    colors = [GREY, ORANGE, BLUE, PURPLE]
    image = Image.new("RGB", (3000, 1700), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (1500, 80), "Hong Kong influenza primary test: paired overall RMSE", 58, TEXT, True, "ma")
    x0, x1, y0, y1 = 650, 2820, 250, 1470
    for tick in range(0, 7):
        x = x0 + (tick / 6.2) * (x1 - x0)
        draw.line((x, y0, x, y1), fill=GRID, width=3)
        text(draw, (x, y1 + 35), str(tick), 30, "#4b5563", anchor="ma")
    row_h = (y1 - y0) / 4
    for index, (label, value, color) in enumerate(zip(labels, values, colors)):
        cy = y0 + row_h * (index + 0.5)
        text(draw, (x0 - 35, cy), label, 38, TEXT, anchor="rm")
        width = (value / 6.2) * (x1 - x0)
        draw.rounded_rectangle((x0, cy - 90, x0 + width, cy + 90), 18, fill=color)
        text(draw, (x0 + width + 30, cy), f"{value:.3f}", 38, TEXT, True, "lm")
    text(draw, ((x0 + x1) / 2, 1600), "RMSE (lower is better)", 38, TEXT, True, "ma")
    save_png(image, SUPP_FIGS[0])

    # Supplementary Figure 2: horizon-specific RMSE.
    selected = ["Baseline", "AWBE", "KG_ADAPTIVE_ENSEMBLE"]
    series = {model: [] for model in selected}
    for row in horizons:
        if row["model"] in series:
            series[row["model"]].append((int(row["week_ahead"]), float(row["rmse"])))
    router = []
    for h in range(9):
        model = "KG_ADAPTIVE_ENSEMBLE" if h == 0 else "AWBE"
        router.append(next(item for item in series[model] if item[0] == h))
    image = Image.new("RGB", (3000, 1700), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (1500, 62), "Primary test RMSE by forecast horizon", 58, TEXT, True, "ma")
    x0, x1, y0, y1 = 240, 2850, 290, 1440
    for tick in range(2, 9):
        y = y1 - ((tick - 1.7) / (8.1 - 1.7)) * (y1 - y0)
        draw.line((x0, y, x1, y), fill=GRID, width=3)
        text(draw, (x0 - 25, y), str(tick), 28, "#4b5563", anchor="rm")
    draw.line((x0, y0, x0, y1), fill=TEXT, width=4)
    draw.line((x0, y1, x1, y1), fill=TEXT, width=4)
    plot_series = [
        ("Paper constant baseline", series["Baseline"], GREY, True),
        ("Paper AWBE", series["AWBE"], BLUE, False),
        ("DiseaseX adaptive ensemble", series["KG_ADAPTIVE_ENSEMBLE"], ORANGE, False),
        ("Horizon-aware router", router, PURPLE, False),
    ]
    for label, points, color, dashed in plot_series:
        coords = []
        for h, value in points:
            x = x0 + (h / 8) * (x1 - x0)
            y = y1 - ((value - 1.7) / (8.1 - 1.7)) * (y1 - y0)
            coords.append((x, y))
        if dashed:
            for a, b in zip(coords[:-1], coords[1:]):
                segments = 12
                for k in range(0, segments, 2):
                    xa = a[0] + (b[0] - a[0]) * k / segments
                    ya = a[1] + (b[1] - a[1]) * k / segments
                    xb = a[0] + (b[0] - a[0]) * (k + 1) / segments
                    yb = a[1] + (b[1] - a[1]) * (k + 1) / segments
                    draw.line((xa, ya, xb, yb), fill=color, width=6)
        else:
            draw.line(coords, fill=color, width=7)
        for x, y in coords:
            draw.ellipse((x - 13, y - 13, x + 13, y + 13), fill=color)
    for h in range(9):
        x = x0 + (h / 8) * (x1 - x0)
        text(draw, (x, y1 + 35), str(h), 30, "#4b5563", anchor="ma")
    legend_y = 175
    legend_x = 260
    for label, _, color, _ in plot_series:
        draw.line((legend_x, legend_y, legend_x + 80, legend_y), fill=color, width=8)
        text(draw, (legend_x + 100, legend_y), label, 29, TEXT, anchor="lm")
        legend_x += 630
    text(draw, ((x0 + x1) / 2, 1600), "Forecast horizon (0 = nowcast; 1–8 = weeks ahead)", 36, TEXT, True, "ma")
    text(draw, (x0, 235), "RMSE (lower is better)", 31, TEXT, True, "la")
    save_png(image, SUPP_FIGS[1])

    # Supplementary Figure 3: overall post-COVID RMSE.
    overall_post = {row["model"]: row for row in post if not row["week_ahead"]}
    labels = ["Paper constant baseline", "DiseaseX adaptive ensemble", "Horizon-aware router", "Paper AWBE"]
    values = [
        float(overall_post["Baseline"]["rmse"]),
        float(overall_post["KG_ADAPTIVE_ENSEMBLE"]["rmse"]),
        float(overall_post["KG_HORIZON_ROUTER"]["rmse"]),
        float(overall_post["AWBE"]["rmse"]),
    ]
    colors = [GREY, ORANGE, PURPLE, BLUE]
    image = Image.new("RGB", (3000, 1700), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (1500, 80), "2023–2024 external post-COVID evaluation", 58, TEXT, True, "ma")
    x0, x1, y0, y1 = 650, 2820, 250, 1470
    for tick in [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5]:
        x = x0 + (tick / 3.7) * (x1 - x0)
        draw.line((x, y0, x, y1), fill=GRID, width=3)
        text(draw, (x, y1 + 35), f"{tick:.1f}", 30, "#4b5563", anchor="ma")
    row_h = (y1 - y0) / 4
    for index, (label, value, color) in enumerate(zip(labels, values, colors)):
        cy = y0 + row_h * (index + 0.5)
        text(draw, (x0 - 35, cy), label, 38, TEXT, anchor="rm")
        width = (value / 3.7) * (x1 - x0)
        draw.rounded_rectangle((x0, cy - 90, x0 + width, cy + 90), 18, fill=color)
        text(draw, (x0 + width + 30, cy), f"{value:.3f}", 38, TEXT, True, "lm")
    text(draw, ((x0 + x1) / 2, 1600), "RMSE (lower is better)", 38, TEXT, True, "ma")
    save_png(image, SUPP_FIGS[2])

    # Supplementary Figure 4: evaluation identities and metric interpretation.
    tracks = [
        ("Track A\nHistorical replay", 77.30, 22.70, BLUE),
        ("Track B\nRepeated window", 72.00, 28.00, "#78909c"),
        ("Track C\nFresh blind", 28.30, 71.70, ORANGE),
        ("Track E\nMain matched set", 66.06, 33.94, "#8064a2"),
        ("Track E\nPost-COVID set", 50.65, 49.35, "#aa8cc5"),
    ]
    image = Image.new("RGB", (3000, 1800), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (1500, 72), "Evaluation identity determines what a score can support", 58, TEXT, True, "ma")
    x0, x1, y0, y1 = 300, 2800, 260, 1300
    for tick in range(0, 101, 20):
        y = y1 - tick / 100 * (y1 - y0)
        draw.line((x0, y, x1, y), fill=GRID, width=3)
        text(draw, (x0 - 30, y), str(tick), 30, "#4b5563", anchor="rm")
    gate_y = y1 - 0.70 * (y1 - y0)
    for start in range(x0, x1, 80):
        draw.line((start, gate_y, min(start + 42, x1), gate_y), fill=RED, width=5)
    text(draw, (x1 - 5, gate_y - 18), "70% agreement gate", 34, RED, True, "rb")
    group = (x1 - x0) / len(tracks)
    for index, (label, agreement, wape, color) in enumerate(tracks):
        cx = x0 + group * (index + 0.5)
        top = y1 - agreement / 100 * (y1 - y0)
        draw.rounded_rectangle((cx - 150, top, cx + 150, y1), 20, fill=color, outline=TEXT, width=3)
        box_top = max(145, top - 155)
        draw.rounded_rectangle((cx - 190, box_top, cx + 190, box_top + 112), 16, fill="white", outline="#cbd5e1", width=2)
        text(draw, (cx, box_top + 30), f"Agreement {agreement:.2f}%", 31, TEXT, True, "ma")
        text(draw, (cx, box_top + 77), f"WAPE {wape:.2f}%", 30, TEXT, True, "ma")
        text(draw, (cx, y1 + 38), label, 34, TEXT, False, "ma")
    draw.rounded_rectangle((1110, 1510, 1770, 1640), 18, fill="#fff2df", outline=ORANGE, width=3)
    text(draw, (1440, 1575), "Fresh window consumed once; gates failed", 29, "#9a4f10", anchor="mm")
    draw.rounded_rectangle((1830, 1490, 2790, 1660), 18, fill="#f2e8f7", outline="#8064a2", width=3)
    text(draw, (2310, 1575), "Nature Communications benchmark\nretrospective method-transfer comparison", 29, "#5f3b72", anchor="mm")
    text(draw, (1500, 1745), "Agreement = max(0, 1 − WAPE) × 100%; it is not classification accuracy.", 28, "#4b5563", anchor="ma")
    save_png(image, SUPP_FIGS[3])

    identity_csv = SI_FIG / "Supplementary_Figure_4_Evaluation_Identity.csv"
    with identity_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["track", "evaluation_identity", "agreement_pct", "wape_pct"])
        for label, agreement, wape, _ in tracks:
            writer.writerow([label.replace("\n", " "), label.split("\n", 1)[1], f"{agreement:.2f}", f"{wape:.2f}"])


def panel(draw: ImageDraw.ImageDraw, box, title: str):
    draw.rounded_rectangle(box, radius=24, fill="#fbfdff", outline="#bdc9d8", width=3)
    text(draw, (box[0] + 32, box[1] + 24), title, 38, NAVY, True)
    return box[0] + 80, box[1] + 105, box[2] - 45, box[3] - 75


def generate_figure6() -> None:
    rows = read_csv(EXP / "Figure_6_Data.csv")
    series = rows[:3]
    macro = {row["label"]: float(row["full_wape"]) * 100 for row in rows[3:]}
    labels = ["AUT Legionellosis", "FIN Hantavirus", "BRA Dengue"]
    full = [float(row["full_wape"]) * 100 for row in series]
    baseline = [float(row["best_baseline_wape"]) * 100 for row in series]
    coverage = [float(row["coverage_80"]) * 100 for row in series]
    precision = [100.0, 86.11, None]
    recall = [100.0, 88.57, None]
    f1 = [100.0, 87.32, None]

    width, height = 3600, 2350
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (width // 2, 62), "Figure 6 | Retrospective evaluation and ablation", 56, TEXT, True, "ma")

    # A: paired forecast error.
    px0, py0, px1, py1 = panel(draw, (110, 190, 1740, 1090), "A  Paired forecast error")
    x0, y0, x1, y1 = px0, py0 + 60, px1, py1 - 95
    for tick in range(0, 31, 5):
        y = y1 - tick / 32 * (y1 - y0)
        draw.line((x0, y, x1, y), fill=GRID, width=2)
        text(draw, (x0 - 18, y), str(tick), 26, "#4b5563", anchor="rm")
    gate = y1 - 30 / 32 * (y1 - y0)
    draw.line((x0, gate, x1, gate), fill=RED, width=4)
    text(draw, (x1 - 5, gate - 12), "30% WAPE gate", 25, RED, True, "rb")
    group = (x1 - x0) / 3
    for index, (value, base, label) in enumerate(zip(full, baseline, labels)):
        cx = x0 + group * (index + 0.5)
        for metric, dx, color in [(value, -55, BLUE), (base, 55, GREY)]:
            top = y1 - metric / 32 * (y1 - y0)
            draw.rectangle((cx + dx - 46, top, cx + dx + 46, y1), fill=color)
            text(draw, (cx + dx, top - 12), f"{metric:.1f}%", 26, TEXT, True, "mb")
        text(draw, (cx, y1 + 28), label, 26, TEXT, anchor="ma")
    draw.rectangle((x0, y1 + 66, x0 + 34, y1 + 100), fill=BLUE)
    text(draw, (x0 + 48, y1 + 83), "DiseaseX governed system", 25, anchor="lm")
    draw.rectangle((x0 + 480, y1 + 66, x0 + 514, y1 + 100), fill=GREY)
    text(draw, (x0 + 528, y1 + 83), "Best fixed baseline", 25, anchor="lm")

    # B: interval coverage.
    px0, py0, px1, py1 = panel(draw, (1860, 190, 3490, 1090), "B  Retrospectively calibrated intervals")
    x0, y0, x1, y1 = px0, py0 + 60, px1, py1 - 95
    band_top = y1 - 90 / 105 * (y1 - y0)
    band_bottom = y1 - 70 / 105 * (y1 - y0)
    draw.rectangle((x0, band_top, x1, band_bottom), fill="#dcebd8")
    for tick in range(0, 101, 20):
        y = y1 - tick / 105 * (y1 - y0)
        draw.line((x0, y, x1, y), fill=GRID, width=2)
        text(draw, (x0 - 18, y), str(tick), 26, "#4b5563", anchor="rm")
    nominal = y1 - 80 / 105 * (y1 - y0)
    draw.line((x0, nominal, x1, nominal), fill=NAVY, width=4)
    text(draw, (x1 - 5, nominal - 12), "Nominal 80%", 25, NAVY, True, "rb")
    group = (x1 - x0) / 3
    for index, (value, label, color) in enumerate(zip(coverage, labels, [BLUE, GREEN, ORANGE])):
        cx = x0 + group * (index + 0.5)
        top = y1 - value / 105 * (y1 - y0)
        draw.rectangle((cx - 110, top, cx + 110, y1), fill=color)
        text(draw, (cx, top - 14), f"{value:.1f}%", 28, TEXT, True, "mb")
        text(draw, (cx, y1 + 28), label, 26, TEXT, anchor="ma")
    text(draw, (x0, y1 + 78), "Prespecified acceptable band: 70–90%", 25, "#2f5d3d")

    # C: warning evaluation. BRA is explicitly not evaluable, not missing.
    px0, py0, px1, py1 = panel(draw, (110, 1170, 1740, 2070), "C  Fixed-threshold early-warning evaluation")
    x0, y0, x1, y1 = px0, py0 + 60, px1, py1 - 105
    for tick in range(0, 101, 20):
        y = y1 - tick / 105 * (y1 - y0)
        draw.line((x0, y, x1, y), fill=GRID, width=2)
        text(draw, (x0 - 18, y), str(tick), 26, "#4b5563", anchor="rm")
    for value, color, label in [(60, "#a85d55", "Precision gate"), (70, "#705a8a", "Recall gate")]:
        y = y1 - value / 105 * (y1 - y0)
        draw.line((x0, y, x1, y), fill=color, width=3)
        text(draw, (x1 - 5, y - 10), f"{label} {value}%", 22, color, True, "rb")
    group = (x1 - x0) / 3
    for index, label in enumerate(labels):
        cx = x0 + group * (index + 0.5)
        if index < 2:
            for value, dx, color in [(precision[index], -80, BLUE), (recall[index], 0, GREEN), (f1[index], 80, ORANGE)]:
                top = y1 - value / 105 * (y1 - y0)
                draw.rectangle((cx + dx - 35, top, cx + dx + 35, y1), fill=color)
                text(draw, (cx + dx, top - 9), f"{value:.1f}", 21, TEXT, True, "mb")
        else:
            draw.rounded_rectangle((cx - 170, y0 + 100, cx + 170, y1 - 25), 18, fill="#edf0f4", outline="#7a8492", width=3)
            text(draw, (cx, (y0 + y1) / 2), "Not evaluable\n(no positive events)", 25, "#4b5563", True, "mm")
        text(draw, (cx, y1 + 28), label, 26, TEXT, anchor="ma")
    for index, (color, label) in enumerate([(BLUE, "Precision"), (GREEN, "Recall"), (ORANGE, "F1")]):
        x = x0 + index * 300
        draw.rectangle((x, y1 + 70, x + 34, y1 + 104), fill=color)
        text(draw, (x + 48, y1 + 87), label, 25, anchor="lm")

    # D: macro ablation.
    px0, py0, px1, py1 = panel(draw, (1860, 1170, 3490, 2070), "D  Macro-WAPE component ablation")
    x0, y0, x1, y1 = px0 + 230, py0 + 45, px1, py1 - 30
    names = ["Full system", "No KG", "Dynamic config", "No feedback", "No proposal"]
    keys = ["full_system", "ablation_no_kg", "ablation_dynamic_execution_config", "ablation_no_feedback", "ablation_no_proposal"]
    values = [macro[key] for key in keys]
    for tick in range(0, 31, 5):
        x = x0 + tick / 31 * (x1 - x0)
        draw.line((x, y0, x, y1), fill=GRID, width=2)
        text(draw, (x, y1 + 18), str(tick), 24, "#4b5563", anchor="ma")
    row = (y1 - y0) / 5
    for index, (name, value) in enumerate(zip(names, values)):
        cy = y0 + row * (index + 0.5)
        text(draw, (x0 - 18, cy), name, 27, TEXT, anchor="rm")
        length = value / 31 * (x1 - x0)
        draw.rectangle((x0, cy - 35, x0 + length, cy + 35), fill=BLUE if index == 0 else "#b9c2cf")
        delta = value - values[0]
        label = f"{value:.2f}%" if index == 0 else f"{value:.2f}%  (Δ {delta:+.2f})"
        text(draw, (x0 + length + 14, cy), label, 24, TEXT, True, "lm")
    text(draw, ((x0 + x1) / 2, y1 + 70), "Macro WAPE (%)", 27, TEXT, True, "ma")

    text(draw, (width // 2, 2208), "BRA Dengue warning scores are undefined because all 13 test origins contained no positive warning event.", 27, RED, False, "ma")
    text(draw, (width // 2, 2260), "Frozen observations; origin-safe horizons 1–4. Retrospective optimization, not fresh held-out evidence.", 27, "#4b5563", False, "ma")
    text(draw, (width // 2, 2310), "Missing periods were neither filled nor interpolated.", 27, "#4b5563", False, "ma")
    save_png(image, NEW_FIG6)
    shutil.copy2(NEW_FIG6, NEW_FIG6_MAIN)
    shutil.copy2(NEW_FIG6, NEW_FIG6_SI)


def replace_docx_media(source: Path, target: Path, replacements: dict[str, Path]) -> dict[str, int]:
    counts = {digest: 0 for digest in replacements}
    with TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(temp)
        media_dir = temp / "word" / "media"
        for member in media_dir.iterdir():
            digest = hashlib.sha256(member.read_bytes()).hexdigest()
            if digest in replacements:
                member.write_bytes(replacements[digest].read_bytes())
                counts[digest] += 1
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member in sorted(temp.rglob("*")):
                if member.is_file():
                    archive.write(member, member.relative_to(temp).as_posix())
    return counts


def replace_paragraph_text(doc_path: Path, replacements: list[tuple[str, str]]) -> int:
    doc = Document(doc_path)
    changed = 0
    for paragraph in doc.paragraphs:
        original = paragraph.text
        updated = original
        for old, new in replacements:
            if old in updated:
                updated = updated.replace(old, new)
        if updated != original:
            paragraph.text = updated
            changed += 1
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    original = paragraph.text
                    updated = original
                    for old, new in replacements:
                        if old in updated:
                            updated = updated.replace(old, new)
                    if updated != original:
                        paragraph.text = updated
                        changed += 1
    doc.save(doc_path)
    return changed


def revise_documents() -> dict[str, object]:
    old_hashes = {
        "fig1": sha256(CURRENT_FIG1),
        "fig3_main": sha256(CURRENT_FIG3),
        "fig3_si": sha256(CURRENT_SI_FIG3),
        "fig6": sha256(CURRENT_FIG6),
        "supp1": sha256(SUPP_FIGS[0]),
        "supp2": sha256(SUPP_FIGS[1]),
        "supp3": sha256(SUPP_FIGS[2]),
        "supp4": sha256(SUPP_FIGS[3]),
    }

    # The supplementary figures have just been regenerated, so capture the old hashes
    # from the input DOCX media by matching the canonical pre-edit files saved below.
    # Caller supplies pre-regeneration hashes through ORIGINAL_SUPP_HASHES.
    old_hashes.update(ORIGINAL_SUPP_HASHES)

    main_map = {
        old_hashes["fig1"]: ORIGINAL_FIG1,
        old_hashes["fig3_main"]: ORIGINAL_FIG3_MAIN,
        old_hashes["fig6"]: NEW_FIG6,
    }
    si_map = {
        old_hashes["fig1"]: ORIGINAL_FIG1,
        old_hashes["fig3_si"]: ORIGINAL_FIG3_SI,
        old_hashes["fig6"]: NEW_FIG6,
        old_hashes["supp1_old"]: SUPP_FIGS[0],
        old_hashes["supp2_old"]: SUPP_FIGS[1],
        old_hashes["supp3_old"]: SUPP_FIGS[2],
        old_hashes["supp4_old"]: SUPP_FIGS[3],
    }
    main_counts = replace_docx_media(MAIN_IN, MAIN_OUT, main_map)
    si_counts = replace_docx_media(SI_IN, SI_OUT, si_map)
    shutil.copy2(MANIFEST_IN, MANIFEST_OUT)

    caption = (
        "Figure 6 | Retrospective evaluation and ablation. DiseaseX achieved WAPE below 30% for all three series. "
        "Early-warning precision, recall and F1 are not evaluable for BRA Dengue because all 13 test origins contained "
        "no positive warning event; these quantities are undefined rather than missing or zero."
    )
    common_replacements = [
        ("Figure 6 | Retrospective multi-pathogen evaluation and ablation", caption),
        ("Figure 6 | Multi-pathogen retrospective optimization and ablation evaluation", caption),
        ("Main Figure 6 | Retrospective multi-pathogen optimization and ablation evaluation", caption),
    ]
    main_text_changes = replace_paragraph_text(MAIN_OUT, common_replacements)
    si_replacements = common_replacements + [
        ("Main Figs. 1, 3 and 6 were rendered deterministically by Python", "Main Figs. 1 and 3 retain the original submission artwork; Main Fig. 6 and Supplementary Figs. 1–4 were rendered deterministically by Python"),
        ("Main Fig. 3 is the existing knowledge-graph illustration", "Main Fig. 2 is the existing knowledge-graph illustration"),
    ]
    si_text_changes = replace_paragraph_text(SI_OUT, si_replacements)
    manifest_replacements = [
        ("Python-generated Figs. 1, 3 and 6", "original Figs. 1 and 3 retained; Fig. 6 and Supplementary Figs. 1–4 generated by Python in English"),
        ("Python-generated Figures 1, 3 and 6", "Original Figures 1 and 3 retained; Figure 6 and Supplementary Figures 1–4 generated by Python in English"),
    ]
    manifest_text_changes = replace_paragraph_text(MANIFEST_OUT, manifest_replacements)

    shutil.copy2(ORIGINAL_FIG1, CURRENT_FIG1)
    shutil.copy2(ORIGINAL_FIG3_MAIN, CURRENT_FIG3)
    shutil.copy2(ORIGINAL_FIG3_SI, CURRENT_SI_FIG3)

    return {
        "main_media_replacements": main_counts,
        "supplementary_media_replacements": si_counts,
        "main_text_changes": main_text_changes,
        "supplementary_text_changes": si_text_changes,
        "manifest_text_changes": manifest_text_changes,
    }


def write_provenance(result: dict[str, object]) -> None:
    record = {
        "revision": "2026-08-24-v3",
        "figure_1": {"status": "RESTORED_ORIGINAL", "sha256": sha256(ORIGINAL_FIG1)},
        "figure_3": {"status": "RESTORED_ORIGINAL", "sha256": sha256(ORIGINAL_FIG3_MAIN)},
        "figure_6": {
            "status": "PYTHON_GENERATED_FROM_FROZEN_CSV",
            "file": str(NEW_FIG6.relative_to(ROOT)),
            "sha256": sha256(NEW_FIG6),
            "bra_warning_status": "NOT_EVALUABLE_NO_EVENT",
            "bra_test_origins": 13,
            "bra_positive_warning_events": 0,
        },
        "supplementary_figures": [
            {"figure": index, "status": "PYTHON_GENERATED_ENGLISH", "file": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for index, path in enumerate(SUPP_FIGS, 1)
        ],
        "knowledge_graph_figure_changed": False,
        "document_edit_result": result,
    }
    (ROOT / "FIGURE_PROVENANCE_20260824_v3.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ORIGINAL_SUPP_HASHES = {
        "supp1_old": sha256(SUPP_FIGS[0]),
        "supp2_old": sha256(SUPP_FIGS[1]),
        "supp3_old": sha256(SUPP_FIGS[2]),
        "supp4_old": sha256(SUPP_FIGS[3]),
    }
    generate_supplementary_figures()
    generate_figure6()
    result = revise_documents()
    write_provenance(result)
    print(json.dumps(result, indent=2))

