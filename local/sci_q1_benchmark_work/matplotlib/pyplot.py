from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


rcParams: dict[str, object] = {}


def _font(size: int, bold: bool = False):
    path = Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc")
    if not path.exists():
        path = Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf")
    return ImageFont.truetype(str(path), size)


@dataclass
class Bar:
    x: float
    y: float
    width: float
    height: float

    def get_y(self):
        return self.y

    def get_height(self):
        return self.height

    def get_x(self):
        return self.x

    def get_width(self):
        return self.width


class Axes:
    def __init__(self):
        self.kind = None
        self.title = ""
        self.xlabel = ""
        self.ylabel = ""
        self.xlim = None
        self.ylim = None
        self.bar_labels = []
        self.bar_values = []
        self.bar_colors = []
        self.lines = []
        self.xtick_positions = []
        self.xtick_labels = []
        self.vlines = []
        self.hlines = []

    def barh(self, labels, values, color=None):
        self.kind = "barh"
        self.bar_labels = list(labels)
        self.bar_values = [float(v) for v in values]
        self.bar_colors = list(color or ["#2563EB"] * len(self.bar_values))
        return [Bar(0.0, float(i), float(v), 0.72) for i, v in enumerate(self.bar_values)]

    def bar(self, labels, values, color=None, width=0.55):
        self.kind = "bar"
        self.bar_labels = list(labels)
        self.bar_values = [float(v) for v in values]
        self.bar_colors = list(color or ["#2563EB"] * len(self.bar_values))
        return [Bar(float(i) - width / 2, 0.0, width, float(v)) for i, v in enumerate(self.bar_values)]

    def plot(self, x, y, marker=None, lw=2, color="#2563EB", label=None, ls="-"):
        self.kind = "line"
        self.lines.append({"x": [float(v) for v in x], "y": [float(v) for v in y], "color": color, "label": label or "", "width": max(2, int(lw * 2)), "dash": ls == "--"})
        return []

    def axvline(self, x, color="#9B1C1C", ls="--", lw=1.4, label=None):
        self.vlines.append((float(x), color, label or ""))

    def axhline(self, y, color="#9B1C1C", ls="--", lw=1.4):
        self.hlines.append((float(y), color))

    def text(self, *args, **kwargs):
        return None

    def set_xlabel(self, text):
        self.xlabel = str(text)

    def set_ylabel(self, text):
        self.ylabel = str(text)

    def set_title(self, text, **kwargs):
        self.title = str(text)

    def set_xlim(self, *values):
        self.xlim = tuple(float(v) for v in values)

    def set_ylim(self, *values):
        self.ylim = tuple(float(v) for v in values)

    def set_xticks(self, positions, labels=None):
        self.xtick_positions = [float(v) for v in positions]
        self.xtick_labels = [str(v) for v in (labels if labels is not None else positions)]

    def grid(self, *args, **kwargs):
        return None

    def legend(self, *args, **kwargs):
        return None


class Figure:
    def __init__(self, ax: Axes, figsize=(10, 5), dpi=180):
        self.ax = ax
        self.width = 1800
        self.height = 950

    def tight_layout(self):
        return None

    def savefig(self, path, **kwargs):
        img = Image.new("RGB", (self.width, self.height), "white")
        draw = ImageDraw.Draw(img)
        title_font = _font(42, True)
        body_font = _font(26)
        small_font = _font(22)
        title_bbox = draw.textbbox((0, 0), self.ax.title, font=title_font)
        draw.text(((self.width - title_bbox[2] + title_bbox[0]) / 2, 34), self.ax.title, fill="#111827", font=title_font)
        if self.ax.kind == "barh":
            self._draw_barh(draw, body_font, small_font)
        elif self.ax.kind == "bar":
            self._draw_bar(draw, body_font, small_font)
        else:
            self._draw_line(draw, body_font, small_font)
        img.save(path)

    def _draw_barh(self, draw, body_font, small_font):
        left, top, right, bottom = 520, 160, 1660, 825
        x_max = self.ax.xlim[1] if self.ax.xlim else max(self.ax.bar_values) * 1.15
        for tick in range(0, int(x_max) + 1, 10):
            x = left + tick / x_max * (right - left)
            draw.line((x, top, x, bottom), fill="#E5E7EB", width=2)
            draw.text((x, bottom + 15), f"{tick}%", fill="#4B5563", font=small_font, anchor="ma")
        for value, color, label in self.ax.vlines:
            x = left + value / x_max * (right - left)
            for y in range(top, bottom, 18):
                draw.line((x, y, x, min(y + 10, bottom)), fill=color, width=3)
            if label:
                draw.text((x + 10, top - 38), label, fill=color, font=small_font)
        bar_h = 74
        gap = 48
        for idx, (label, value, color) in enumerate(zip(self.ax.bar_labels, self.ax.bar_values, self.ax.bar_colors, strict=True)):
            y = top + idx * (bar_h + gap)
            draw.text((left - 24, y + bar_h / 2), label, fill="#111827", font=body_font, anchor="rm")
            x2 = left + value / x_max * (right - left)
            draw.rounded_rectangle((left, y, x2, y + bar_h), radius=12, fill=color)
            draw.text((x2 + 15, y + bar_h / 2), f"{value:.2f}%", fill="#111827", font=body_font, anchor="lm")
        draw.text(((left + right) / 2, 900), self.ax.xlabel, fill="#374151", font=body_font, anchor="mm")

    def _draw_bar(self, draw, body_font, small_font):
        left, top, right, bottom = 220, 170, 1660, 800
        y_max = self.ax.ylim[1] if self.ax.ylim else max(self.ax.bar_values) * 1.2
        for tick in range(0, int(y_max) + 1, 10):
            y = bottom - tick / y_max * (bottom - top)
            draw.line((left, y, right, y), fill="#E5E7EB", width=2)
            draw.text((left - 18, y), f"{tick}%", fill="#4B5563", font=small_font, anchor="rm")
        for value, color in self.ax.hlines:
            y = bottom - value / y_max * (bottom - top)
            for x in range(left, right, 24):
                draw.line((x, y, min(x + 14, right), y), fill=color, width=3)
        centers = [700, 1250] if len(self.ax.bar_values) == 2 else [left + (i + 1) * (right - left) / (len(self.ax.bar_values) + 1) for i in range(len(self.ax.bar_values))]
        for center, label, value, color in zip(centers, self.ax.bar_labels, self.ax.bar_values, self.ax.bar_colors, strict=True):
            y = bottom - value / y_max * (bottom - top)
            draw.rounded_rectangle((center - 150, y, center + 150, bottom), radius=14, fill=color)
            draw.text((center, y - 18), f"{value:.2f}%", fill="#111827", font=body_font, anchor="ms")
            for line_idx, line in enumerate(str(label).split("\n")):
                draw.text((center, bottom + 25 + line_idx * 34), line, fill="#374151", font=body_font, anchor="ma")
        draw.text((30, (top + bottom) / 2), self.ax.ylabel, fill="#374151", font=body_font, anchor="lm")

    def _draw_line(self, draw, body_font, small_font):
        left, top, right, bottom = 175, 190, 1690, 800
        all_y = [v for line in self.ax.lines for v in line["y"]]
        y_max = self.ax.ylim[1] if self.ax.ylim else max(all_y) * 1.08
        y_step = 100 if y_max > 500 else 50
        for tick in range(0, int(y_max) + 1, y_step):
            y = bottom - tick / y_max * (bottom - top)
            draw.line((left, y, right, y), fill="#E5E7EB", width=2)
            draw.text((left - 18, y), str(tick), fill="#4B5563", font=small_font, anchor="rm")
        x_values = self.ax.lines[0]["x"]
        x_min, x_max = min(x_values), max(x_values)
        x_labels = self.ax.xtick_labels or [str(int(v)) for v in x_values]
        x_positions = self.ax.xtick_positions or x_values
        for x_value, label in zip(x_positions, x_labels, strict=True):
            x = left + (x_value - x_min) / max(1e-9, x_max - x_min) * (right - left)
            draw.text((x, bottom + 16), label, fill="#4B5563", font=small_font, anchor="ma")
        for line in self.ax.lines:
            points = []
            for x_value, y_value in zip(line["x"], line["y"], strict=True):
                x = left + (x_value - x_min) / max(1e-9, x_max - x_min) * (right - left)
                y = bottom - y_value / y_max * (bottom - top)
                points.append((x, y))
            draw.line(points, fill=line["color"], width=line["width"], joint="curve")
            for x, y in points:
                draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=line["color"])
        items = [(line["label"], line["color"]) for line in self.ax.lines if line["label"]]
        start_x = 180
        for idx, (label, color) in enumerate(items):
            col = idx % 2
            row = idx // 2
            x = start_x + col * 650
            y = 110 + row * 36
            draw.line((x, y + 12, x + 55, y + 12), fill=color, width=7)
            draw.text((x + 68, y), label, fill="#374151", font=small_font)
        if self.ax.ylabel:
            draw.text((25, (top + bottom) / 2), self.ax.ylabel, fill="#374151", font=body_font, anchor="lm")
        if self.ax.xlabel:
            draw.text(((left + right) / 2, 900), self.ax.xlabel, fill="#374151", font=body_font, anchor="mm")


def subplots(figsize=(10, 5), dpi=180):
    ax = Axes()
    return Figure(ax, figsize=figsize, dpi=dpi), ax


def close(fig=None):
    return None
