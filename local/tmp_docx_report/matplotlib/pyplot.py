from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class _Spine:
    def set_visible(self, value):
        return None


class _Axes:
    def __init__(self, figure):
        self.figure = figure
        self.labels = []
        self.values = []
        self.colors = []
        self.title = ""
        self.xlabel = ""
        self.notes = []
        self.spines = {name: _Spine() for name in ("top", "right", "left", "bottom")}

    def barh(self, y, values, color=None, height=None):
        self.values = list(values)
        self.colors = list(color or ["#2E74B5"] * len(self.values))

    def set_yticks(self, y, labels=None, **kwargs):
        self.labels = list(labels or [])

    def invert_yaxis(self):
        return None

    def set_xlabel(self, text, **kwargs):
        self.xlabel = text

    def set_title(self, text, **kwargs):
        self.title = text

    def set_xlim(self, *args):
        return None

    def grid(self, **kwargs):
        return None

    def set_axisbelow(self, value):
        return None

    def text(self, x, y, text, **kwargs):
        self.notes.append((x, y, text))


class _Figure:
    def __init__(self):
        self.axes = _Axes(self)
        self.footer = ""

    def text(self, x, y, text, **kwargs):
        self.footer = text

    def savefig(self, path, **kwargs):
        labels = self.axes.labels
        values = self.axes.values
        colors = self.axes.colors
        width, height = 1530, 760
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        regular_path = Path(r"C:\Windows\Fonts\msyh.ttc")
        bold_path = Path(r"C:\Windows\Fonts\msyhbd.ttc")
        regular = ImageFont.truetype(str(regular_path), 30)
        small = ImageFont.truetype(str(regular_path), 24)
        title_font = ImageFont.truetype(str(bold_path if bold_path.exists() else regular_path), 38)
        draw.text((width // 2, 35), self.axes.title, fill="#172B4D", font=title_font, anchor="ma")
        left, right, top, row_h = 330, 1400, 130, 82
        axis_max = 35.0
        for tick in range(0, 36, 5):
            x = left + int((right - left) * tick / axis_max)
            draw.line((x, top - 10, x, top + row_h * len(labels) - 18), fill="#D8DEE9", width=2)
            draw.text((x, top + row_h * len(labels) - 5), str(tick), fill="#5B6573", font=small, anchor="ma")
        for idx, (label, value, color) in enumerate(zip(labels, values, colors)):
            y = top + idx * row_h
            draw.text((left - 22, y + 24), label, fill="#172B4D", font=regular, anchor="ra")
            bar_w = int((right - left) * value / axis_max)
            draw.rounded_rectangle((left, y, left + bar_w, y + 48), radius=8, fill=color)
            draw.text((left + bar_w + 14, y + 24), f"{value:.2f}%", fill="#172B4D", font=small, anchor="lm")
        draw.text(((left + right) // 2, 670), self.axes.xlabel, fill="#172B4D", font=regular, anchor="ma")
        draw.text((width // 2, 718), self.footer, fill="#5B6573", font=small, anchor="ma")
        image.save(path)


def subplots(*args, **kwargs):
    fig = _Figure()
    return fig, fig.axes


def tight_layout(*args, **kwargs):
    return None


def close(*args, **kwargs):
    return None
