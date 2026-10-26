from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(r"F:\作业\aibola\论文材料包_20260813")
DIAGRAM_DIR = ROOT / "02_架构图"
RESULT_DIR = ROOT / "03_实验结果"


FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD_PATH = Path(r"C:\Windows\Fonts\msyhbd.ttc")


def pil_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD_PATH if bold else FONT_PATH), size=size)


def draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, font: ImageFont.FreeTypeFont, fill: str, max_width: int, line_gap: int = 10) -> int:
    x, y = xy
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for char in paragraph:
            trial = current + char
            if current and draw.textlength(trial, font=font) > max_width:
                lines.append(current)
                current = char
            else:
                current = trial
        lines.append(current)
    line_h = font.size + line_gap
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    return y


class Drawio:
    def __init__(self, title: str, width: int = 1400, height: int = 900):
        self.title = title
        self.width = width
        self.height = height
        self.cells: list[str] = []
        self.next_id = 2

    def vertex(
        self,
        label: str,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        fill: str = "#F4F7FB",
        stroke: str = "#5B6B82",
        font: str = "#172235",
        rounded: bool = True,
        size: int = 16,
        bold: bool = False,
        shape: str | None = None,
        parent: str = "1",
        align: str = "center",
    ) -> str:
        cid = str(self.next_id)
        self.next_id += 1
        if shape == "cylinder":
            prefix = "shape=cylinder3;"
        elif shape == "diamond":
            prefix = "rhombus;"
        else:
            prefix = f"rounded={1 if rounded else 0};"
        style = (
            f"{prefix}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
            f"fontColor={font};fontSize={size};fontFamily=Microsoft YaHei;align={align};"
            f"verticalAlign=middle;spacing=8;shadow=0;strokeWidth=1.5;"
        )
        if bold:
            style += "fontStyle=1;"
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" vertex="1" parent="{parent}">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />'
            "</mxCell>"
        )
        return cid

    def text(self, label: str, x: int, y: int, w: int, h: int, *, size: int = 18, color: str = "#172235", bold: bool = False, align: str = "left") -> str:
        cid = str(self.next_id)
        self.next_id += 1
        style = (
            "text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;"
            f"fontColor={color};fontSize={size};fontFamily=Microsoft YaHei;align={align};verticalAlign=middle;"
        )
        if bold:
            style += "fontStyle=1;"
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />'
            "</mxCell>"
        )
        return cid

    def edge(
        self,
        source: str,
        target: str,
        label: str = "",
        *,
        dashed: bool = False,
        color: str = "#64748B",
        exit_xy: tuple[float, float] | None = None,
        entry_xy: tuple[float, float] | None = None,
        points: list[tuple[int, int]] | None = None,
    ) -> str:
        cid = str(self.next_id)
        self.next_id += 1
        style = (
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
            f"strokeColor={color};strokeWidth=1.8;endArrow=block;endFill=1;fontFamily=Microsoft YaHei;fontSize=12;"
        )
        if dashed:
            style += "dashed=1;dashPattern=5 5;"
        if exit_xy:
            style += f"exitX={exit_xy[0]};exitY={exit_xy[1]};exitDx=0;exitDy=0;"
        if entry_xy:
            style += f"entryX={entry_xy[0]};entryY={entry_xy[1]};entryDx=0;entryDy=0;"
        geometry = '<mxGeometry relative="1" as="geometry" />'
        if points:
            point_xml = "".join(f'<mxPoint x="{x}" y="{y}" />' for x, y in points)
            geometry = f'<mxGeometry relative="1" as="geometry"><Array as="points">{point_xml}</Array></mxGeometry>'
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{source}" target="{target}">'
            f'{geometry}'
            "</mxCell>"
        )
        return cid

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mxfile host="drawio" version="30.2.6">\n'
            f'  <diagram name="{escape(self.title)}">\n'
            f'    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0">\n'
            "      <root>\n        <mxCell id=\"0\" />\n        <mxCell id=\"1\" parent=\"0\" />\n        "
            + "\n        ".join(self.cells)
            + "\n      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n"
        )
        path.write_text(xml, encoding="utf-8")


def project_architecture() -> None:
    d = Drawio("项目整体架构", 1500, 920)
    d.text("疫情自动获取系统：可信数据、知识图谱与预测闭环", 60, 30, 1380, 55, size=28, bold=True, align="center")
    d.text("所有自动化结果均保留来源、版本、时间边界与人工复核状态", 60, 85, 1380, 35, size=14, color="#526076", align="center")

    headers = [
        ("① 官方数据源", 70, "#EAF2FF", "#3B82F6"),
        ("② 数据治理层", 340, "#ECFDF5", "#10B981"),
        ("③ 知识图谱层", 610, "#F5F0FF", "#8B5CF6"),
        ("④ 预测执行层", 880, "#FFF7E8", "#F59E0B"),
        ("⑤ 应用与反馈", 1150, "#FFF1F2", "#F43F5E"),
    ]
    header_ids = []
    for label, x, fill, stroke in headers:
        header_ids.append(d.vertex(label, x, 150, 230, 55, fill=fill, stroke=stroke, bold=True, size=18))

    source1 = d.vertex("WHO / CDC / FluNet\n政府开放数据目录", 90, 245, 190, 85, fill="#EAF2FF", stroke="#3B82F6", bold=True)
    source2 = d.vertex("论文 PDF 与元数据\n本地受控导入", 90, 370, 190, 85, fill="#EAF2FF", stroke="#3B82F6")
    source3 = d.vertex("人工审核候选\nPreview / Staging", 90, 495, 190, 85, fill="#EAF2FF", stroke="#3B82F6")

    data1 = d.vertex("原始文件归档\nURL · 时间 · SHA-256", 360, 245, 190, 85, fill="#ECFDF5", stroke="#10B981", bold=True)
    data2 = d.vertex("清洗与规范化\n病种 · 国家 · 周期 · 指标", 360, 370, 190, 85, fill="#ECFDF5", stroke="#10B981")
    data3 = d.vertex("PostgreSQL 分层存储\nRaw / Trusted / Preview / Forecast", 360, 495, 190, 95, fill="#ECFDF5", stroke="#10B981", shape="cylinder")

    kg1 = d.vertex("论文与证据层\nWork · PDF · Locator · Claim", 630, 235, 190, 95, fill="#F5F0FF", stroke="#8B5CF6", bold=True)
    kg2 = d.vertex("关系与语义层\n疾病—模型—参数—适用条件", 630, 370, 190, 95, fill="#F5F0FF", stroke="#8B5CF6")
    kg3 = d.vertex("推荐与执行合同\n画像评分 · 模型候选 · 理由", 630, 505, 190, 95, fill="#F5F0FF", stroke="#8B5CF6")

    pred1 = d.vertex("确定性回测引擎\nRolling-origin · Baselines", 900, 235, 190, 95, fill="#FFF7E8", stroke="#F59E0B", bold=True)
    pred2 = d.vertex("Qwen 受控调参\n白名单模型与参数空间", 900, 370, 190, 95, fill="#FFF7E8", stroke="#F59E0B")
    pred3 = d.vertex("预测与不确定性\nWAPE · 区间覆盖率 · 溯源", 900, 505, 190, 95, fill="#FFF7E8", stroke="#F59E0B")

    app1 = d.vertex("只读 API 与 Dashboard\n三联图 · 推荐 · 预测页面", 1170, 235, 190, 95, fill="#FFF1F2", stroke="#F43F5E", bold=True)
    app2 = d.vertex("Review Console\n人工批准 / 驳回 / 补证", 1170, 370, 190, 95, fill="#FFF1F2", stroke="#F43F5E")
    app3 = d.vertex("反馈与自学习账本\n不直接改 Trusted / Active", 1170, 505, 190, 95, fill="#FFF1F2", stroke="#F43F5E")

    d.edge(source1, data1, "采集")
    d.edge(source2, kg1, "全文与元数据", exit_xy=(1, 0.5), entry_xy=(0, 0.5), points=[(320, 335), (590, 335)])
    d.edge(source3, data2, "人工复核后")
    d.edge(data1, data2, "校验")
    d.edge(data2, data3, "分层写入")
    d.edge(data3, kg3, "数据画像")
    d.edge(kg1, kg2, "证据绑定")
    d.edge(kg2, kg3, "规则推理")
    d.edge(kg3, pred1, "执行合同")
    d.edge(pred1, pred2, "验证集反馈")
    d.edge(pred2, pred3, "确定性训练")
    d.edge(pred3, app1, "只读发布")
    d.edge(app2, kg2, "人工决定", dashed=True, exit_xy=(0, 0.5), entry_xy=(1, 0.5), points=[(1140, 475), (850, 475)])
    d.edge(app3, kg3, "经治理的反馈", dashed=True, exit_xy=(0.5, 1), entry_xy=(0.5, 1), points=[(1265, 650), (725, 650)])

    governance = d.vertex(
        "横切治理：鉴权与最小权限  ·  来源与哈希溯源  ·  Held-out 全局一次消费  ·  未来泄漏哨兵  ·  幂等与回滚  ·  人工复核",
        90,
        700,
        1270,
        85,
        fill="#162033",
        stroke="#162033",
        font="#FFFFFF",
        bold=True,
        size=17,
    )
    d.text("图注：Qwen 只提出受约束的模型/参数方案；训练、回测、数据写入和发布门禁均由确定性代码执行。", 100, 815, 1250, 45, size=14, color="#526076", align="center")
    d.write(DIAGRAM_DIR / "图1_项目整体架构.drawio")


def kg_architecture() -> None:
    d = Drawio("知识图谱逻辑架构", 1450, 920)
    d.text("预测模型知识图谱：证据存储—关系推理—问答推荐三层架构", 50, 30, 1350, 55, size=27, bold=True, align="center")
    d.text("图谱不是“直接训练模型”的黑箱，而是把论文证据、数据画像与可执行合同连接起来", 80, 85, 1290, 35, size=14, color="#526076", align="center")

    # Layer 1
    d.vertex("数据存储与证据层", 70, 155, 250, 55, fill="#EAF2FF", stroke="#3B82F6", bold=True, size=18)
    work = d.vertex("Work / DOI\n论文身份", 90, 250, 180, 75, fill="#EAF2FF", stroke="#3B82F6")
    doc = d.vertex("DocumentVersion\nPDF 与全文版本", 90, 365, 180, 75, fill="#EAF2FF", stroke="#3B82F6")
    evidence = d.vertex("EvidenceSpan / Locator\n页码 · Offset · 双 SHA", 90, 480, 180, 90, fill="#EAF2FF", stroke="#3B82F6", bold=True)
    profile = d.vertex("DataProfile\n国家 · 病种 · 指标 · 频率", 90, 610, 180, 90, fill="#EAF2FF", stroke="#3B82F6")

    # Layer 2
    d.vertex("关系与语义层", 420, 155, 540, 55, fill="#F5F0FF", stroke="#8B5CF6", bold=True, size=18)
    disease = d.vertex("Disease\n病种机制与传播特征", 440, 245, 210, 80, fill="#F5F0FF", stroke="#8B5CF6")
    model = d.vertex("Model / Method\n模型族与实现", 710, 245, 210, 80, fill="#F5F0FF", stroke="#8B5CF6")
    claim = d.vertex("Assertion\n当前论文使用 / 提出 / 比较", 440, 380, 210, 85, fill="#F5F0FF", stroke="#8B5CF6", bold=True)
    params = d.vertex("ParameterEvidence\n参数值 · 来源 · 适用条件", 710, 380, 210, 85, fill="#F5F0FF", stroke="#8B5CF6")
    scope = d.vertex("ApplicabilityScope\n地理 · 时间 · 人群 · 目标", 440, 515, 210, 85, fill="#F5F0FF", stroke="#8B5CF6")
    score = d.vertex("EvidenceScore\nA–F 维度与缺口解释", 710, 515, 210, 85, fill="#F5F0FF", stroke="#8B5CF6")
    contract = d.vertex("ExecutionContract\n模型 · 特征 · 参数 · 风险门禁", 575, 655, 220, 90, fill="#F5F0FF", stroke="#8B5CF6", bold=True)

    # Layer 3
    d.vertex("问答、推荐与审计层", 1060, 155, 300, 55, fill="#FFF7E8", stroke="#F59E0B", bold=True, size=18)
    query = d.vertex("用户输入\n国家 + 病种 + 指标", 1120, 250, 190, 75, fill="#FFF7E8", stroke="#F59E0B")
    guidance = d.vertex("Guidance API\n候选模型 + 中文理由", 1120, 380, 190, 85, fill="#FFF7E8", stroke="#F59E0B", bold=True)
    provenance = d.vertex("论文溯源\nDOI · 原文证据 · Locator", 1120, 515, 190, 85, fill="#FFF7E8", stroke="#F59E0B")
    review = d.vertex("人工复核与反馈\n批准 Claim ≠ 允许正式预测", 1120, 655, 190, 90, fill="#FFF7E8", stroke="#F59E0B")

    d.edge(work, doc)
    d.edge(doc, evidence)
    d.edge(evidence, claim, "原文证据", exit_xy=(1, 0.5), entry_xy=(0, 0.5))
    d.edge(profile, scope, "数据画像")
    d.edge(disease, claim, exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    d.edge(model, claim, exit_xy=(0, 0.5), entry_xy=(1, 0.5))
    d.edge(claim, params, exit_xy=(1, 0.5), entry_xy=(0, 0.5))
    d.edge(scope, score, exit_xy=(1, 0.5), entry_xy=(0, 0.5))
    d.edge(params, score)
    d.edge(score, contract, "门禁通过", exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    d.edge(query, guidance, "检索", exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    d.edge(contract, guidance, "推荐", exit_xy=(1, 0.5), entry_xy=(0, 0.5), points=[(980, 700), (1000, 420)])
    d.edge(guidance, provenance, "解释", exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    d.edge(review, claim, "人工决定", dashed=True, exit_xy=(0.5, 1), entry_xy=(0.5, 1), points=[(1215, 780), (380, 780), (380, 485), (545, 485)])
    d.edge(review, contract, "授权边界", dashed=True, exit_xy=(0, 0.5), entry_xy=(1, 0.5))

    d.text("当前资产规模：1,574 个唯一全文版本 · 4,502 个证据片段 · 3,791 条断言 · 26 条数据画像 · 17 条可执行推荐", 120, 815, 1210, 45, size=15, color="#334155", bold=True, align="center")
    d.write(DIAGRAM_DIR / "图2_知识图谱逻辑架构.drawio")


def experiment_architecture() -> None:
    d = Drawio("2024论文对比实验架构", 1500, 940)
    d.text("2024 年新论文方法对比实验：统一数据、统一切分、统一回测", 70, 30, 1360, 55, size=27, bold=True, align="center")
    d.text("比较对象：论文 AR(2) 方程 vs 项目模型 vs 验证集选出的组合模型 vs Last-value 基线", 80, 85, 1340, 35, size=14, color="#526076", align="center")

    source = d.vertex("新加坡政府官方周度登革热数据\n2012-W01—2019-W48\n412 个规范化周点", 80, 250, 220, 110, fill="#EAF2FF", stroke="#3B82F6", bold=True)
    align = d.vertex("论文口径重建\n去重 · 周键校验 · 来源哈希\n保留重建假设", 360, 250, 220, 110, fill="#ECFDF5", stroke="#10B981")
    split = d.vertex("时间切分\nWarm-up 1\nTrain 311 · Validation 50 · Test 50", 640, 250, 230, 110, fill="#F5F0FF", stroke="#8B5CF6", bold=True)

    ar2 = d.vertex("论文 AR(2)\n固定公开系数\n单变量两阶滞后", 270, 470, 210, 105, fill="#FFF7E8", stroke="#F59E0B")
    project = d.vertex("项目模型\nlagged_count_boosting.v1\n验证集内 64 组参数", 520, 470, 230, 105, fill="#FFF7E8", stroke="#F59E0B", bold=True)
    ensemble = d.vertex("项目组合模型\n0.25 AR(2) + 0.55 项目模型\n+ 0.20 Last-value", 790, 470, 250, 105, fill="#FFF7E8", stroke="#F59E0B")
    baseline = d.vertex("Last-value 基线\n上一周实际值", 1080, 470, 200, 105, fill="#FFF7E8", stroke="#F59E0B")

    protocol = d.vertex("统一 Rolling one-step 协议\n预测当前周后才揭示该周实际值\n所有方法同一批 target week", 500, 655, 360, 105, fill="#162033", stroke="#162033", font="#FFFFFF", bold=True)
    metrics = d.vertex("独立测试集评估\nWAPE ↓ · 吻合度 ↑ · RMSE ↓\n配对预测与可复算 SHA-256", 970, 655, 300, 105, fill="#FFF1F2", stroke="#F43F5E", bold=True)

    d.edge(source, align, "官方来源")
    d.edge(align, split, "固定时序", exit_xy=(1, 0.5), entry_xy=(0, 0.5))
    split_targets = [
        (ar2, 0.18),
        (project, 0.40),
        (ensemble, 0.62),
        (baseline, 0.84),
    ]
    for target, exit_x in split_targets:
        d.edge(split, target, "相同切分", exit_xy=(exit_x, 1), entry_xy=(0.5, 0))
        d.edge(target, protocol, exit_xy=(0.5, 1), entry_xy=(0.5, 0))
    d.edge(protocol, metrics, "一次性 Test")

    d.vertex("验证门禁", 80, 660, 300, 50, fill="#F4F7FB", stroke="#64748B", bold=True, size=18)
    d.text("• 组合权重只在 Validation 上选择\n• Test 标签在最终评估前保持未打开\n• 比较按 (week, prediction, actual) 严格配对\n• 独立重放 4/4 哈希一致", 90, 720, 360, 125, size=14, color="#334155")
    d.text("结论：项目组合模型测试 WAPE=12.80%，吻合度=87.20%；论文 AR(2) 为 11.34% / 88.66%。项目处于接近性能层级，但未超过论文方法。", 170, 865, 1160, 45, size=15, color="#334155", bold=True, align="center")
    d.write(DIAGRAM_DIR / "图3_2024论文对比实验架构.drawio")


def make_card(title: str, subtitle: str, metrics: list[tuple[str, str]], status: str, status_color: str, narrative: str, path: Path) -> None:
    image = Image.new("RGB", (1800, 1040), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((55, 55, 1745, 985), radius=32, fill="#F8FAFC", outline="#CBD5E1", width=3)
    draw.text((115, 105), title, font=pil_font(45, True), fill="#0F172A")
    draw.text((115, 180), subtitle, font=pil_font(24), fill="#475569")
    draw.rounded_rectangle((1390, 105, 1645, 175), radius=16, fill=status_color)
    bbox = draw.textbbox((0, 0), status, font=pil_font(23, True))
    draw.text((1517 - (bbox[2] - bbox[0]) / 2, 140 - (bbox[3] - bbox[1]) / 2), status, font=pil_font(23, True), fill="#FFFFFF")

    for i, (name, value) in enumerate(metrics):
        col = i % 3
        row = i // 3
        x1 = 115 + col * 525
        y1 = 300 + row * 205
        draw.rounded_rectangle((x1, y1, x1 + 465, y1 + 155), radius=20, fill="#FFFFFF", outline="#D7E0EC", width=2)
        draw.text((x1 + 25, y1 + 22), name, font=pil_font(22), fill="#64748B")
        draw.text((x1 + 25, y1 + 75), value, font=pil_font(36, True), fill="#1D4ED8")
    draw.text((115, 735), "解释", font=pil_font(26, True), fill="#0F172A")
    draw_wrapped(draw, (115, 785), narrative, font=pil_font(22), fill="#334155", max_width=1520, line_gap=13)
    draw.text((115, 935), "证据来源：已验收 API/浏览器审计结果；本图为论文结果卡，不是实时网页截屏。", font=pil_font(17), fill="#64748B")
    image.save(path, quality=95)


def result_cards() -> None:
    cards = [
        (
            "预测链路 A｜奥地利军团菌（月度）",
            "历史回放轨道 · 受控 Qwen 参数建议 · Champion：seasonal_negbin_glm.v1",
            [("WAPE（越低越好）", "22.7%"), ("预测吻合度", "77.3%"), ("区间覆盖率", "100%"), ("MAE", "6.584"), ("RMSE", "9.011"), ("Fresh held-out", "否")],
            "历史回放",
            "#64748B",
            "该链路证明了知识图谱推荐、Qwen 受控调参、确定性回测和结果展示可以贯通。\n由于测试窗口已被历史运行使用，它只能作为机制演示，不能称为新的独立盲测认证。",
            "预测链路A_奥地利军团菌_结果图.png",
        ),
        (
            "预测链路 B｜芬兰汉坦病毒（周度）",
            "历史重复评估轨道 · Champion：lagged_count_boosting.v1",
            [("WAPE（越低越好）", "28.0%"), ("预测吻合度", "72.0%"), ("区间覆盖率", "97.9%"), ("MAE", "6.716"), ("RMSE", "8.323"), ("Fresh held-out", "否")],
            "重复评估",
            "#64748B",
            "该轨道达到 70% 以上吻合度，并真实优于同样本基线；但同一窗口曾被多个运行打开，\n因此已被治理迁移重分类为 RETROSPECTIVE_REPLAY_NOT_FRESH，不作为独立盲测证据。",
            "预测链路B_芬兰汉坦病毒_结果图.png",
        ),
        (
            "预测链路 C｜智利流感（周度）",
            "新鲜留存轨道 · 全局只消费一次 · Champion：lagged_count_boosting.v1",
            [("WAPE（越低越好）", "71.7%"), ("预测吻合度", "28.3%"), ("区间覆盖率", "89.6%"), ("MAE", "212.330"), ("RMSE", "387.039"), ("Fresh held-out", "是")],
            "门禁未通过",
            "#DC2626",
            "这是当前唯一按全局注册表锁定并一次性消费的新鲜盲测窗口。区间覆盖率合格，且相对最佳基线有改善，\n但 WAPE 与吻合度未达到认证阈值，因此系统诚实保留结果而不宣称“70% 认证”。",
            "预测链路C_智利流感_结果图.png",
        ),
    ]
    for title, subtitle, metrics, status, color, narrative, filename in cards:
        make_card(title, subtitle, metrics, status, color, narrative, RESULT_DIR / filename)


def comparison_chart() -> None:
    data = json.loads((RESULT_DIR / "comparison_2024_method.json").read_text(encoding="utf-8"))
    labels = ["论文 AR(2)", "项目原始模型", "项目组合模型", "Last-value 基线"]
    wape = [
        data["ar2_method"]["reproduced_test_metrics"]["wape"] * 100,
        data["project_method"]["test_metrics"]["wape"] * 100,
        data["project_optimized_ensemble"]["test_metrics"]["wape"] * 100,
        data["last_value_baseline"]["test_metrics"]["wape"] * 100,
    ]
    agreement = [100 - x for x in wape]
    rmse = [
        data["ar2_method"]["reproduced_test_metrics"]["rmse"],
        data["project_method"]["test_metrics"]["rmse"],
        data["project_optimized_ensemble"]["test_metrics"]["rmse"],
        data["last_value_baseline"]["test_metrics"]["rmse"],
    ]
    colors = ["#2563EB", "#F59E0B", "#8B5CF6", "#64748B"]
    image = Image.new("RGB", (2200, 1180), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.text((1100, 55), "2024 年新论文方法与本项目模型的同数据、同切分、同协议对比", font=pil_font(42, True), fill="#0F172A", anchor="ma")
    titles = ["测试集 WAPE（越低越好）", "预测吻合度（1 − WAPE）", "测试集 RMSE（越低越好）"]
    series = [wape, agreement, rmse]
    suffixes = ["%", "%", ""]
    lefts = [70, 790, 1510]
    panel_w = 620
    for panel_x, title, vals, suffix in zip(lefts, titles, series, suffixes):
        draw.rounded_rectangle((panel_x, 145, panel_x + panel_w, 1010), radius=25, fill="#F8FAFC", outline="#D7E0EC", width=2)
        draw.text((panel_x + panel_w / 2, 190), title, font=pil_font(27, True), fill="#0F172A", anchor="ma")
        max_v = max(vals) * 1.12
        for i, (label, val, color) in enumerate(zip(labels, vals, colors)):
            y = 310 + i * 165
            draw.text((panel_x + 28, y), label, font=pil_font(21), fill="#334155")
            bar_x = panel_x + 28
            bar_y = y + 48
            bar_w = int((panel_w - 110) * val / max_v)
            draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 52), radius=12, fill=color)
            draw.text((bar_x + bar_w + 16, bar_y + 26), f"{val:.2f}{suffix}", font=pil_font(20, True), fill="#0F172A", anchor="lm")
    draw.text((1100, 1090), "数据：新加坡政府周度登革热数据（2012–2019）；Test=50 周。项目组合模型接近论文 AR(2)，但未超过。", font=pil_font(22), fill="#475569", anchor="ma")
    image.save(RESULT_DIR / "图4_2024论文对比实验结果.png", quality=95)


if __name__ == "__main__":
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    project_architecture()
    kg_architecture()
    experiment_architecture()
    result_cards()
    comparison_chart()
    print("assets_generated")
