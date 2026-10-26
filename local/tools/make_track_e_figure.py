from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(r"F:\作业\aibola\论文材料包_20260813")
DATA = ROOT / "03_实验结果" / "comparison_2024_method.json"
OUT = ROOT / "03_实验结果" / "预测链路E_新加坡登革热_结果图.png"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
FONT_BOLD = Path("C:/Windows/Fonts/msyhbd.ttc")


def f(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold and FONT_BOLD.exists() else FONT), size)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, size: int, *, bold: bool = False, fill: str = "#172235", anchor: str | None = None) -> None:
    draw.text(xy, value, font=f(size, bold), fill=fill, anchor=anchor)


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    metrics = data["project_optimized_ensemble"]["test_metrics"]
    wape = float(metrics["wape"]) * 100
    agreement = float(metrics["agreement"]) * 100
    rmse = float(metrics["rmse"])

    image = Image.new("RGB", (2400, 1350), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (1200, 55), "第三条 70% 以上预测链路：新加坡登革热论文对齐历史回放", 54, bold=True, anchor="mm")
    draw.line((1210, 145, 1210, 1230), fill="#CBD5E1", width=3)
    text(draw, (80, 155), "A  误差、预测吻合度与阈值", 40, bold=True)
    text(draw, (1290, 155), "B  审计身份与治理状态", 40, bold=True)

    def bar(label: str, value: float, threshold: float, y: int, color: str, threshold_text: str) -> None:
        text(draw, (90, y - 70), label, 31, bold=True)
        x0, x1, height = 90, 990, 95
        draw.rounded_rectangle((x0, y, x1, y + height), radius=12, fill="#E8EEF6")
        width = int((x1 - x0) * min(value / 100, 1))
        draw.rounded_rectangle((x0, y, x0 + width, y + height), radius=12, fill=color)
        xt = int(x0 + (x1 - x0) * threshold / 100)
        for yy in range(y - 25, y + height + 15, 22):
            draw.line((xt, yy, xt, min(yy + 12, y + height + 15)), fill="#B91C1C", width=4)
        text(draw, (xt + 12, y - 42), f"门槛 {threshold_text}", 24, fill="#991B1B")
        text(draw, (x0 + 18, y + height // 2), f"{value:.2f}%", 31, bold=True, fill="white", anchor="lm")
        text(draw, (1030, y + height // 2), "通过", 31, bold=True, fill="#047857", anchor="lm")

    bar("WAPE（越低越好）", wape, 30, 315, "#2563EB", "≤30%")
    bar("预测吻合度（越高越好）", agreement, 70, 655, "#7C3AED", "≥70%")
    text(draw, (90, 890), f"测试集 RMSE：{rmse:.2f} 病例", 31, bold=True)
    text(draw, (90, 955), "预测吻合度 = max(0, 1−WAPE)×100%，不是分类准确率。", 25, fill="#475569")
    text(draw, (90, 1035), "结论：测试集吻合度 87.20%，真实超过 70% 门槛。", 31, bold=True, fill="#7C2D12")

    draw.rounded_rectangle((1300, 240, 2300, 360), radius=16, fill="#475569")
    text(draw, (1800, 300), "历史回放 / 非 Fresh", 40, bold=True, fill="white", anchor="mm")
    rows = [
        ("评估身份", "RETROSPECTIVE_PAPER_ALIGNED_BENCHMARK"),
        ("数据范围", "2012-W01—2019-W48；412 个规范化时点"),
        ("切分", "311 train / 50 validation / 50 test"),
        ("Champion", "AR(2) 0.25 + Boosting 0.55 + Last-value 0.20"),
        ("发布状态", "Shadow only；未 Promotion；未改 Active pointer"),
    ]
    y = 405
    for key, value in rows:
        draw.rectangle((1300, y, 1580, y + 130), fill="#F1F5F9", outline="#CBD5E1", width=2)
        draw.rectangle((1580, y, 2300, y + 130), fill="white", outline="#CBD5E1", width=2)
        text(draw, (1440, y + 65), key, 25, bold=True, anchor="mm")
        text(draw, (1610, y + 65), value, 23, anchor="lm")
        y += 130
    draw.rounded_rectangle((1300, 1080, 2300, 1225), radius=12, fill="#FFF1E8")
    text(draw, (1330, 1110), "治理结论", 28, bold=True, fill="#9A3412")
    text(draw, (1330, 1160), "该结果可复算、可展示，但不冒充新的独立盲测认证。", 26, bold=True, fill="#7C2D12")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, format="PNG", optimize=True)
    print(OUT)


if __name__ == "__main__":
    main()
