from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "figures"
FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
FALLBACK_FONT = Path(r"C:\Windows\Fonts\simhei.ttf")


@dataclass(frozen=True)
class Module:
    slug: str
    zh: str
    en: str
    phase_or_layer: str
    description: str
    color: str
    accent: str
    status: str = "current"


@dataclass(frozen=True)
class FigurePart:
    slug: str
    zh: str
    en: str
    figure: str
    group: str
    description: str
    svg: Path
    png: Path
    width: int
    height: int
    is_full: bool
    is_part: bool
    order: int
    transparent: bool
    suggested_use: str
    layout: dict[str, Any] | None = None


ROADMAP_PHASES = [
    {
        "key": "m1",
        "title": "里程碑一：自动多源数据采集和对齐",
        "goal": "实现多源疫情数据的自动发现、受控采集、清洗、对齐与质量管理",
        "color": "#1F6FEB",
        "light": "#EAF2FF",
        "border": "#BBD3FF",
        "modules": [
            ("source_discovery", "数据源发现", "Source Discovery Agent", "基于国家和病种发现候选数据源，默认进入人工复核。"),
            ("source_registry", "数据源注册与治理", "Source Registry", "维护候选源、状态、评分、健康和合规元数据。"),
            ("access_guardrails", "访问控制与合规检查", "Access Guardrails", "在 live fetch 前进行 robots、条款、限速和人工审核闸门。"),
            ("connectors", "多源采集连接器", "Connectors", "抽象 CDC / WHO / ECDC / 文献 / 数据库 / 新闻等来源。"),
            ("raw_documents", "原始文档存储", "Raw Documents", "保存抓取或 fixture 文档、hash、来源和解析状态。"),
            ("cleaner_agent", "数据清洗与结构化抽取", "Cleaner Agent", "从文档中抽取病例、日期、病种、地区和上下文。"),
            ("epidemic_events", "事件级标准化", "Epidemic Events", "形成统一的疫情事件表和来源追溯。"),
            ("feature_engineering", "特征工程基础层", "Feature Engineering", "聚合周粒度特征、滞后特征、质量和季节信号。"),
            ("data_quality", "数据质量报告", "Data Quality Agent", "输出数据质量、标签审计和可复核风险提示。"),
        ],
    },
    {
        "key": "m2",
        "title": "里程碑二：自动寻找最优模型和特征选择",
        "goal": "针对指定病种，自动发现候选模型、候选特征和建模策略，并完成评估筛选",
        "color": "#8B5CF6",
        "light": "#F3ECFF",
        "border": "#D8C4FF",
        "modules": [
            ("disease_task", "病种任务定义", "Disease Task Definition", "明确国家、病种、时间范围、预测目标和评估口径。", "planned"),
            ("literature_discovery", "文献与数据库检索代理", "Literature / Database Discovery Agent", "发现可用于建模的公开知识、文献和数据库线索。", "planned"),
            ("candidate_model_pool", "候选模型库构建", "Candidate Model Pool", "形成可比较的 baseline、统计模型和后续增强模型池。", "planned"),
            ("candidate_feature_pool", "候选特征库构建", "Candidate Feature Pool", "整理病例史、环境、流动性、报告质量等候选特征。", "planned"),
            ("feature_selection", "自动特征选择", "Feature Selection", "评估特征子集对预测和解释性的贡献。", "planned"),
            ("model_benchmarking", "自动模型比较", "Model Benchmarking", "统一指标下比较候选模型和参数策略。", "planned"),
            ("backtesting", "回测评估", "Backtesting", "使用 rolling-origin 回测评估误差、不确定性和分类风险。"),
            ("minimum_data", "最小数据集合分析", "Minimum Data Analyzer", "估计达标预测所需的最小历史窗口和特征组合。"),
            ("strategy_review", "人工审核与策略确认", "Human Review", "由分析人员确认候选模型、特征和上线策略。"),
        ],
    },
    {
        "key": "m3",
        "title": "里程碑三：迭代更新并优化模型预测",
        "goal": "将优选模型与自动化数据流结合，形成持续反馈、持续学习和持续优化的预测闭环",
        "color": "#2E9D64",
        "light": "#EAF8F0",
        "border": "#BFE8D1",
        "modules": [
            ("best_model_deployment", "最优模型部署", "Best Model Deployment", "将人工确认后的优选模型作为受治理的预测组件。", "planned"),
            ("feature_refresh", "自动化特征更新", "Automated Feature Refresh", "按任务流刷新最新可用特征与数据质量摘要。"),
            ("prediction_scoring", "预测与风险分级", "Prediction & Risk Scoring", "输出预测值、不确定性区间、风险等级和复核状态。"),
            ("human_feedback", "人工反馈采集", "Human Feedback", "记录成功、部分成功、失败、真实值和失败原因。"),
            ("failure_analysis", "失败分析", "Failure Analysis", "识别低估、高估、漏报、误报、数据缺口和时序误差。"),
            ("experience_bank", "经验库", "Experience Bank", "沉淀可复用经验、错误分类和后续改进建议。"),
            ("model_governance", "模型治理与升级判断", "Model Governance", "提供 champion/challenger 离线晋级建议，不自动替换生产模型。"),
            ("iterative_loop", "迭代优化闭环", "Iterative Optimization Loop", "围绕反馈、质量、模型和数据源形成可控优化循环。", "planned"),
            ("dashboard_api", "可视化与决策支持", "Dashboard & API", "通过 API 和 Dashboard 支持演示、复核和业务操作。"),
        ],
    },
]


ARCH_LAYERS = [
    (
        "external",
        "外部数据与知识源",
        "#EEF5FF",
        "#4C7BD9",
        [
            ("external_sources", "官方数据源", "CDC / WHO / ECDC", "官方或半官方公开数据源抽象表示。"),
            ("database_resources", "数据库资源", "Databases", "公开数据目录、结构化数据集和统计资源。"),
            ("literature_resources", "文献资源", "Literature", "论文、报告和知识来源，当前作为规划增强能力。", "planned"),
            ("news_pages", "新闻与公开网页", "News / Public Web", "公开网页和新闻线索，需受控采集与人工复核。"),
            ("user_task", "用户输入任务", "User Task", "国家、病种、时间范围和分析目标。"),
        ],
    ),
    (
        "collection",
        "数据源发现与采集层",
        "#F3ECFF",
        "#8B5CF6",
        [
            ("source_discovery_agent", "Source Discovery Agent", "Source Discovery Agent", "fixture/mock 候选源发现、评分、入库。"),
            ("source_registry", "Source Registry", "Source Registry", "数据源治理、状态、评分和健康管理中心。"),
            ("access_guardrails", "Access Guardrails", "Access Guardrails", "live fetch / live discovery 的合规闸门。"),
            ("crawler_agent", "Crawler Agent / Connectors", "Crawler Agent / Connectors", "受控连接器和 fixture/mock 抓取入口。"),
            ("crawler_optimizer_agent", "Crawler Optimizer Agent", "Crawler Optimizer Agent", "失败诊断、解析优化建议和连接器规格建议。"),
        ],
    ),
    (
        "processing",
        "数据处理与质量层",
        "#EAF8F0",
        "#2E9D64",
        [
            ("raw_document_store", "Raw Document Store", "Raw Document Store", "原始文档、hash、URL、来源和解析状态。"),
            ("cleaner_agent", "Cleaner Agent", "Cleaner Agent", "文本清洗、事件抽取、病种与地区识别。"),
            ("epidemic_event_store", "Epidemic Event Store", "Epidemic Event Store", "统一事件表、病例、日期、地理与来源追溯。"),
            ("feature_builder", "Feature Builder", "Feature Builder", "周粒度特征、滞后特征、质量和季节特征。"),
            ("data_quality_agent", "Data Quality Agent", "Data Quality Agent", "数据画像、质量报告和缺口分析。"),
            ("label_audit", "Label Audit / Quality Report", "Label Audit / Quality Report", "标签一致性、泄漏检查和质量复核。"),
        ],
    ),
    (
        "modeling",
        "模型与学习层",
        "#FFF6E8",
        "#E28A20",
        [
            ("candidate_model_pool", "Candidate Model Pool", "Candidate Model Pool", "候选模型集合，部分属于规划增强能力。", "planned"),
            ("feature_selection", "Feature Selection", "Feature Selection", "候选特征筛选与贡献评估，规划增强能力。", "planned"),
            ("predictor_agent", "Predictor Agent", "Predictor Agent", "baseline 预测、滚动回测、预测结果入库。"),
            ("backtesting_engine", "Backtesting Engine", "Backtesting Engine", "rolling-origin 回测和指标摘要。"),
            ("minimum_data_analyzer", "Minimum Data Analyzer", "Minimum Data Analyzer", "最小历史窗口与特征集合分析。"),
            ("feedback_service", "Feedback Service", "Feedback Service", "人工反馈、真实值和失败原因记录。"),
            ("failure_analysis", "Failure Analysis", "Failure Analysis", "低估、高估、漏报、误报等失败归因。"),
            ("experience_bank", "Experience Bank", "Experience Bank", "经验记录、建议动作和后续分析元数据。"),
            ("model_governance", "Model Governance", "Model Governance", "离线晋级检查，不自动替换模型。"),
        ],
    ),
    (
        "services",
        "服务与编排层",
        "#F2F4F7",
        "#596579",
        [
            ("worker_scheduler", "Worker / Scheduler", "Worker / Scheduler", "本地同步任务和 schedule 配置骨架。"),
            ("job_registry", "Job Registry", "Job Registry", "任务状态和安全摘要。"),
            ("pipeline_orchestrator", "Pipeline Orchestrator", "Pipeline Orchestrator", "串联本地端到端 smoke 和业务任务。"),
            ("fastapi_api", "FastAPI Business API", "FastAPI Business API", "统一业务接口和安全输出。"),
        ],
    ),
    (
        "interaction",
        "交互与展示层",
        "#F7FAFC",
        "#1F6FEB",
        [
            ("dashboard", "Dashboard", "Dashboard", "Streamlit 本地可视化入口。"),
            ("source_discovery_ui", "Source Discovery UI", "Source Discovery UI", "粘贴 mock search results，查看候选源和规格建议。"),
            ("crawler_optimizer_ui", "Crawler Optimizer UI", "Crawler Optimizer UI", "查看爬虫失败诊断和优化建议。"),
            ("data_quality_ui", "Data Quality UI", "Data Quality UI", "查看数据质量、标签审计和报告摘要。"),
            ("model_prediction_ui", "Model / Prediction UI", "Model / Prediction UI", "查看预测、回测和模型晋级建议。"),
            ("human_review", "Human Review", "Human Review", "人工复核、反馈和治理确认入口。"),
        ],
    ),
]


def main() -> int:
    make_dirs()
    generated_at = datetime.now(UTC).isoformat()
    roadmap = generate_roadmap(generated_at)
    architecture = generate_architecture(generated_at)
    write_readme(roadmap, architecture)
    summary = {
        "status": "ok",
        "roadmap_parts": len([p for p in roadmap if p.is_part]),
        "architecture_parts": len([p for p in architecture if p.is_part]),
        "roadmap_full": "reports/figures/roadmap/full/technology_roadmap_full.svg",
        "architecture_full": "reports/figures/architecture/full/system_architecture_full.svg",
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def make_dirs() -> None:
    for directory in (
        OUT / "roadmap" / "full",
        OUT / "roadmap" / "parts",
        OUT / "architecture" / "full",
        OUT / "architecture" / "parts",
        OUT / "manifests",
        OUT / "previews",
    ):
        directory.mkdir(parents=True, exist_ok=True)


def generate_roadmap(generated_at: str) -> list[FigurePart]:
    width, height = 2400, 1350
    full_svg = OUT / "roadmap" / "full" / "technology_roadmap_full.svg"
    full_png = OUT / "roadmap" / "full" / "technology_roadmap_full.png"
    parts: list[FigurePart] = []
    layout: dict[str, dict[str, Any]] = {}

    svg = SvgCanvas(width, height, background="#FFFFFF")
    svg.defs()
    svg.text(1200, 66, "项目技术路线图", 50, "#182334", weight="bold", anchor="middle")
    svg.text(1200, 120, "从自动多源数据采集到自动优化预测模型的三阶段路线", 28, "#516070", anchor="middle")
    svg.round_rect(420, 155, 1560, 58, 28, "#F7FAFC", "#D8E0EA", 2)
    svg.text(1200, 192, "总目标：构建面向传染病监测与预测的可治理智能体系统", 26, "#26374A", anchor="middle")

    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    font_title = font(50, bold=True)
    font_subtitle = font(28)
    font_body = font(24)
    draw_center_text(draw, (1200, 38), "项目技术路线图", font_title, "#182334", width=1300)
    draw_center_text(draw, (1200, 101), "从自动多源数据采集到自动优化预测模型的三阶段路线", font_subtitle, "#516070", width=1500)
    draw.rounded_rectangle([420, 155, 1980, 213], radius=28, fill="#F7FAFC", outline="#D8E0EA", width=2)
    draw_center_text(draw, (1200, 170), "总目标：构建面向传染病监测与预测的可治理智能体系统", font_body, "#26374A", width=1500)

    container_w = 690
    container_h = 960
    container_y = 290
    xs = [90, 855, 1620]
    for index, phase in enumerate(ROADMAP_PHASES, start=1):
        x = xs[index - 1]
        draw_milestone(svg, draw, x, container_y, container_w, container_h, phase)
        phase_slug = f"milestone_{index}_container"
        parts.append(
            make_container_part(
                slug=phase_slug,
                zh=f"里程碑{index}容器",
                en=f"Milestone {index} Container",
                figure="roadmap",
                group=phase["title"],
                title=phase["title"],
                subtitle=phase["goal"],
                color=phase["color"],
                light=phase["light"],
                border=phase["border"],
                order=100 + index,
            )
        )
        layout[phase_slug] = layout_item(x, container_y, container_w, container_h, 2)
        module_positions = module_grid_positions(x + 32, container_y + 220, container_w - 64, 650, 3, 3)
        for module_index, module_data in enumerate(phase["modules"], start=1):
            slug, zh, en, desc, *rest = module_data
            status = rest[0] if rest else "current"
            module = Module(
                slug=f"roadmap_{phase['key']}_{slug}",
                zh=zh,
                en=en,
                phase_or_layer=phase["title"],
                description=desc,
                color=phase["light"],
                accent=phase["color"],
                status=status,
            )
            mx, my, mw, mh = module_positions[module_index - 1]
            draw_module(svg, draw, mx, my, mw, mh, module.zh, module.en, module.accent, module.color, status=module.status)
            layout[module.slug] = layout_item(mx, my, mw, mh, 4)
            parts.append(make_module_part(module, figure="roadmap", group=phase["title"], order=index * 100 + module_index))

    arrow_specs = [
        ("roadmap_arrow_1_to_2", 785, 760, "#6B7280", "阶段推进"),
        ("roadmap_arrow_2_to_3", 1550, 760, "#6B7280", "闭环增强"),
    ]
    for order, (slug, x, y, color, label) in enumerate(arrow_specs, start=1):
        draw_arrow(svg, draw, x, y, x + 70, y, color=color, label=label)
        parts.append(make_arrow_part(slug, "路线推进箭头", "Roadmap Arrow", "roadmap", "连接箭头", color, order=2000 + order))
        layout[slug] = layout_item(x, y - 34, 110, 68, 5)

    parts.append(
        make_title_part(
            slug="roadmap_title_block",
            zh="路线图标题块",
            en="Roadmap Title Block",
            figure="roadmap",
            title="项目技术路线图",
            subtitle="从自动多源数据采集到自动优化预测模型的三阶段路线",
            order=3000,
        )
    )
    layout["roadmap_title_block"] = layout_item(300, 30, 1800, 200, 6)

    full_svg.write_text(svg.render(), encoding="utf-8")
    image.save(full_png)
    shutil.copyfile(full_png, OUT / "previews" / "roadmap_preview.png")

    full_part = FigurePart(
        slug="technology_roadmap_full",
        zh="项目技术路线图整图",
        en="Technology Roadmap Full Figure",
        figure="roadmap",
        group="full",
        description="三阶段技术路线整图，适合直接放入 PPT 或论文汇报。",
        svg=full_svg,
        png=full_png,
        width=width,
        height=height,
        is_full=True,
        is_part=False,
        order=0,
        transparent=False,
        suggested_use="整页 PPT 或报告主图",
        layout=layout_item(0, 0, width, height, 0),
    )
    entries = [full_part, *parts]
    write_manifest("technology_roadmap", entries, layout, generated_at)
    return entries


def generate_architecture(generated_at: str) -> list[FigurePart]:
    width, height = 2400, 1600
    full_svg = OUT / "architecture" / "full" / "system_architecture_full.svg"
    full_png = OUT / "architecture" / "full" / "system_architecture_full.png"
    parts: list[FigurePart] = []
    layout: dict[str, dict[str, Any]] = {}

    svg = SvgCanvas(width, height, background="#FFFFFF")
    svg.defs()
    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)

    svg.text(1200, 65, "项目系统架构图", 50, "#182334", weight="bold", anchor="middle")
    svg.text(1200, 120, "面向传染病监测与预测的可治理多智能体系统架构", 28, "#516070", anchor="middle")
    draw_center_text(draw, (1200, 38), "项目系统架构图", font(50, bold=True), "#182334", width=1200)
    draw_center_text(draw, (1200, 101), "面向传染病监测与预测的可治理多智能体系统架构", font(28), "#516070", width=1500)

    y = 185
    layer_heights = {
        "external": 155,
        "collection": 155,
        "processing": 170,
        "modeling": 250,
        "services": 155,
        "interaction": 170,
    }
    layer_gap = 18
    for layer_order, (layer_key, title, fill, accent, modules) in enumerate(ARCH_LAYERS, start=1):
        h = layer_heights[layer_key]
        draw_layer(svg, draw, 90, y, 2220, h, title, fill, accent)
        layer_slug = f"arch_layer_{layer_key}"
        parts.append(
            make_layer_part(
                slug=layer_slug,
                zh=title,
                en=f"{layer_key.title()} Layer",
                figure="architecture",
                group=title,
                color=accent,
                light=fill,
                order=1000 + layer_order,
            )
        )
        layout[layer_slug] = layout_item(90, y, 2220, h, 1)
        module_positions = architecture_positions(layer_key, y, h, len(modules))
        for module_order, module_data in enumerate(modules, start=1):
            slug, zh, en, desc, *rest = module_data
            status = rest[0] if rest else "current"
            module = Module(
                slug=f"arch_{slug}",
                zh=zh,
                en=en,
                phase_or_layer=title,
                description=desc,
                color="#FFFFFF",
                accent=accent,
                status=status,
            )
            mx, my, mw, mh = module_positions[module_order - 1]
            draw_module(svg, draw, mx, my, mw, mh, module.zh, module.en, accent, "#FFFFFF", status=module.status)
            layout[module.slug] = layout_item(mx, my, mw, mh, 3)
            parts.append(make_module_part(module, figure="architecture", group=title, order=layer_order * 100 + module_order))
        if layer_order < len(ARCH_LAYERS):
            draw_arrow(svg, draw, 1200, y + h + 3, 1200, y + h + layer_gap - 3, color="#94A3B8", label=None)
        y += h + layer_gap

    draw_arrow(svg, draw, 1780, 892, 1060, 802, color="#D97706", label=None)
    draw_arrow(svg, draw, 472, 425, 472, 335, color="#7C3AED", label="合规控制")
    svg.round_rect(1388, 818, 122, 32, 16, "#FFF7ED", "#FDBA74", 1)
    svg.text(1449, 840, "反馈闭环", 19, "#8A4B06", weight="bold", anchor="middle")
    draw.rounded_rectangle([1388, 818, 1510, 850], radius=16, fill="#FFF7ED", outline="#FDBA74", width=1)
    draw_center_text(draw, (1449, 822), "反馈闭环", font(19, bold=True), "#8A4B06", width=112)
    draw.text((500, 360), "Access Guardrails 控制 live fetch", fill="#6D28D9", font=font(20, bold=True))
    svg.text(500, 368, "Access Guardrails 控制 live fetch", 20, "#6D28D9", weight="bold")

    parts.append(make_arrow_part("arch_arrow_dataflow", "数据流箭头", "Dataflow Arrow", "architecture", "连接箭头", "#94A3B8", order=3000))
    parts.append(make_arrow_part("arch_arrow_feedback_loop", "反馈闭环箭头", "Feedback Loop Arrow", "architecture", "连接箭头", "#D97706", order=3001, curved=True))
    parts.append(
        make_title_part(
            slug="arch_title_block",
            zh="架构图标题块",
            en="Architecture Title Block",
            figure="architecture",
            title="项目系统架构图",
            subtitle="面向传染病监测与预测的可治理多智能体系统架构",
            order=3002,
        )
    )
    layout["arch_arrow_dataflow"] = layout_item(1170, 340, 60, 760, 4)
    layout["arch_arrow_feedback_loop"] = layout_item(1180, 820, 700, 260, 5)
    layout["arch_title_block"] = layout_item(300, 30, 1800, 200, 6)

    full_svg.write_text(svg.render(), encoding="utf-8")
    image.save(full_png)
    shutil.copyfile(full_png, OUT / "previews" / "architecture_preview.png")

    full_part = FigurePart(
        slug="system_architecture_full",
        zh="项目系统架构图整图",
        en="System Architecture Full Figure",
        figure="architecture",
        group="full",
        description="分层系统架构整图，展示数据流、控制流、反馈闭环和交互入口。",
        svg=full_svg,
        png=full_png,
        width=width,
        height=height,
        is_full=True,
        is_part=False,
        order=0,
        transparent=False,
        suggested_use="整页 PPT 或架构说明主图",
        layout=layout_item(0, 0, width, height, 0),
    )
    entries = [full_part, *parts]
    write_manifest("system_architecture", entries, layout, generated_at)
    return entries


def draw_milestone(svg: "SvgCanvas", draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, phase: dict[str, Any]) -> None:
    svg.round_rect(x, y, w, h, 26, phase["light"], phase["border"], 3)
    svg.round_rect(x, y, w, 86, 26, phase["color"], phase["color"], 2)
    svg.text(x + w / 2, y + 46, phase["title"], 27, "#FFFFFF", weight="bold", anchor="middle")
    svg.text_block(x + 36, y + 120, w - 72, phase["goal"], 21, "#334155", max_chars=26)

    draw.rounded_rectangle([x, y, x + w, y + h], radius=26, fill=phase["light"], outline=phase["border"], width=3)
    draw.rounded_rectangle([x, y, x + w, y + 86], radius=26, fill=phase["color"], outline=phase["color"], width=2)
    draw_center_text(draw, (x + w / 2, y + 20), phase["title"], font(27, bold=True), "#FFFFFF", width=w - 50)
    draw_wrapped_text(draw, (x + 36, y + 112), phase["goal"], font(21), "#334155", max_width=w - 72, line_spacing=7)


def draw_layer(svg: "SvgCanvas", draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, title: str, fill: str, accent: str) -> None:
    svg.round_rect(x, y, w, h, 20, fill, "#D7DEE8", 2)
    svg.round_rect(x + 18, y + 24, 280, h - 48, 18, "#FFFFFF", accent, 3)
    svg.text(x + 158, y + h / 2 - 6, title, 25, accent, weight="bold", anchor="middle")

    draw.rounded_rectangle([x, y, x + w, y + h], radius=20, fill=fill, outline="#D7DEE8", width=2)
    draw.rounded_rectangle([x + 18, y + 24, x + 298, y + h - 24], radius=18, fill="#FFFFFF", outline=accent, width=3)
    draw_center_text(draw, (x + 158, y + h / 2 - 36), title, font(25, bold=True), accent, width=250)


def draw_module(
    svg: "SvgCanvas",
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    zh: str,
    en: str,
    accent: str,
    fill: str,
    *,
    status: str,
) -> None:
    border = "#CBD5E1" if status == "current" else "#D4C7F9"
    svg.round_rect(x, y, w, h, 16, fill, border, 2)
    svg.circle(x + 26, y + 28, 13, accent)
    if status != "current":
        svg.round_rect(x + w - 68, y + 10, 52, 24, 12, "#FFF7ED", "#FDBA74", 1)
        svg.text(x + w - 42, y + 27, "规划", 14, "#9A3412", weight="bold", anchor="middle")
    svg.text_block(x + 52, y + 18, w - 78, zh, 19, "#182334", max_chars=12, weight="bold")
    svg.text_block(x + 52, y + h - 38, w - 78, en, 13, "#64748B", max_chars=22)

    draw.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=fill, outline=border, width=2)
    draw.ellipse([x + 13, y + 15, x + 39, y + 41], fill=accent)
    if status != "current":
        draw.rounded_rectangle([x + w - 68, y + 10, x + w - 16, y + 34], radius=12, fill="#FFF7ED", outline="#FDBA74", width=1)
        draw_center_text(draw, (x + w - 42, y + 13), "规划", font(14, bold=True), "#9A3412", width=48)
    draw_wrapped_text(draw, (x + 52, y + 15), zh, font(19, bold=True), "#182334", max_width=w - 82, line_spacing=4)
    draw_wrapped_text(draw, (x + 52, y + h - 39), en, font(13), "#64748B", max_width=w - 82, line_spacing=3)


def draw_arrow(
    svg: "SvgCanvas",
    draw: ImageDraw.ImageDraw,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str,
    label: str | None,
) -> None:
    svg.line_arrow(x1, y1, x2, y2, color, 4)
    draw_arrow_png(draw, x1, y1, x2, y2, color)
    if label:
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2 - 24
        svg.text(mid_x, mid_y, label, 18, color, weight="bold", anchor="middle")
        draw_center_text(draw, (mid_x, mid_y - 12), label, font(18, bold=True), color, width=160)


def module_grid_positions(x: int, y: int, w: int, h: int, cols: int, rows: int) -> list[tuple[int, int, int, int]]:
    gap_x, gap_y = 18, 18
    card_w = int((w - gap_x * (cols - 1)) / cols)
    card_h = int((h - gap_y * (rows - 1)) / rows)
    return [
        (x + col * (card_w + gap_x), y + row * (card_h + gap_y), card_w, card_h)
        for row in range(rows)
        for col in range(cols)
    ]


def architecture_positions(layer_key: str, y: int, h: int, count: int) -> list[tuple[int, int, int, int]]:
    left, right = 420, 2265
    area_w = right - left
    if layer_key == "modeling":
        cols_top = 5
        gap_x, gap_y = 18, 16
        card_w = int((area_w - gap_x * (cols_top - 1)) / cols_top)
        card_h = 74
        top_y = y + 42
        bottom_y = top_y + card_h + gap_y
        positions: list[tuple[int, int, int, int]] = []
        for idx in range(count):
            if idx < 5:
                col = idx
                positions.append((left + col * (card_w + gap_x), top_y, card_w, card_h))
            else:
                col = idx - 5
                offset = int((card_w + gap_x) / 2)
                positions.append((left + offset + col * (card_w + gap_x), bottom_y, card_w, card_h))
        return positions
    gap_x = 18
    card_w = int((area_w - gap_x * (count - 1)) / count)
    card_h = min(78, h - 58)
    card_y = y + int((h - card_h) / 2)
    return [(left + idx * (card_w + gap_x), card_y, card_w, card_h) for idx in range(count)]


def make_module_part(module: Module, *, figure: str, group: str, order: int) -> FigurePart:
    width, height = 520, 160
    svg_path = OUT / figure / "parts" / f"{module.slug}.svg"
    png_path = OUT / figure / "parts" / f"{module.slug}.png"
    svg = SvgCanvas(width, height, background=None)
    svg.defs()
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw_module(svg, draw, 8, 8, width - 16, height - 16, module.zh, module.en, module.accent, module.color, status=module.status)
    svg_path.write_text(svg.render(), encoding="utf-8")
    image.save(png_path)
    return FigurePart(
        slug=module.slug,
        zh=module.zh,
        en=module.en,
        figure=figure,
        group=group,
        description=module.description,
        svg=svg_path,
        png=png_path,
        width=width,
        height=height,
        is_full=False,
        is_part=True,
        order=order,
        transparent=True,
        suggested_use="PPT 模块卡片，可单独拖拽组合",
        layout=None,
    )


def make_container_part(
    *,
    slug: str,
    zh: str,
    en: str,
    figure: str,
    group: str,
    title: str,
    subtitle: str,
    color: str,
    light: str,
    border: str,
    order: int,
) -> FigurePart:
    width, height = 760, 420
    svg_path = OUT / figure / "parts" / f"{slug}.svg"
    png_path = OUT / figure / "parts" / f"{slug}.png"
    svg = SvgCanvas(width, height, background=None)
    svg.defs()
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    phase = {"title": title, "goal": subtitle, "color": color, "light": light, "border": border}
    draw_milestone(svg, draw, 8, 8, width - 16, height - 16, phase)
    svg_path.write_text(svg.render(), encoding="utf-8")
    image.save(png_path)
    return FigurePart(
        slug=slug,
        zh=zh,
        en=en,
        figure=figure,
        group=group,
        description=f"{title} 的可复用容器素材。",
        svg=svg_path,
        png=png_path,
        width=width,
        height=height,
        is_full=False,
        is_part=True,
        order=order,
        transparent=True,
        suggested_use="PPT 中作为阶段容器或背景框",
    )


def make_layer_part(
    *,
    slug: str,
    zh: str,
    en: str,
    figure: str,
    group: str,
    color: str,
    light: str,
    order: int,
) -> FigurePart:
    width, height = 900, 180
    svg_path = OUT / figure / "parts" / f"{slug}.svg"
    png_path = OUT / figure / "parts" / f"{slug}.png"
    svg = SvgCanvas(width, height, background=None)
    svg.defs()
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw_layer(svg, draw, 8, 8, width - 16, height - 16, zh, light, color)
    svg_path.write_text(svg.render(), encoding="utf-8")
    image.save(png_path)
    return FigurePart(
        slug=slug,
        zh=zh,
        en=en,
        figure=figure,
        group=group,
        description=f"{zh} 的可复用分层容器素材。",
        svg=svg_path,
        png=png_path,
        width=width,
        height=height,
        is_full=False,
        is_part=True,
        order=order,
        transparent=True,
        suggested_use="PPT 中作为架构层背景或分组容器",
    )


def make_arrow_part(
    slug: str,
    zh: str,
    en: str,
    figure: str,
    group: str,
    color: str,
    *,
    order: int,
    curved: bool = False,
) -> FigurePart:
    width, height = 360, 110
    svg_path = OUT / figure / "parts" / f"{slug}.svg"
    png_path = OUT / figure / "parts" / f"{slug}.png"
    svg = SvgCanvas(width, height, background=None)
    svg.defs()
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    if curved:
        svg.path("M 34 84 C 140 20, 230 20, 322 72", color, 5)
        draw_arc_arrow(draw, color)
    else:
        draw_arrow(svg, draw, 32, 55, 328, 55, color=color, label=None)
    svg_path.write_text(svg.render(), encoding="utf-8")
    image.save(png_path)
    return FigurePart(
        slug=slug,
        zh=zh,
        en=en,
        figure=figure,
        group=group,
        description=f"{zh}，用于在 PPT 中连接模块。",
        svg=svg_path,
        png=png_path,
        width=width,
        height=height,
        is_full=False,
        is_part=True,
        order=order,
        transparent=True,
        suggested_use="PPT 中作为连接箭头",
    )


def make_title_part(
    *,
    slug: str,
    zh: str,
    en: str,
    figure: str,
    title: str,
    subtitle: str,
    order: int,
) -> FigurePart:
    width, height = 1200, 190
    svg_path = OUT / figure / "parts" / f"{slug}.svg"
    png_path = OUT / figure / "parts" / f"{slug}.png"
    svg = SvgCanvas(width, height, background=None)
    svg.defs()
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    svg.round_rect(20, 20, width - 40, height - 40, 24, "#FFFFFF", "#D8E0EA", 2)
    svg.text(width / 2, 76, title, 42, "#182334", weight="bold", anchor="middle")
    svg.text(width / 2, 126, subtitle, 23, "#516070", anchor="middle")
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=24, fill="#FFFFFF", outline="#D8E0EA", width=2)
    draw_center_text(draw, (width / 2, 50), title, font(42, bold=True), "#182334", width=1100)
    draw_center_text(draw, (width / 2, 111), subtitle, font(23), "#516070", width=1100)
    svg_path.write_text(svg.render(), encoding="utf-8")
    image.save(png_path)
    return FigurePart(
        slug=slug,
        zh=zh,
        en=en,
        figure=figure,
        group="title",
        description="可复用标题块，适合放在 PPT 顶部。",
        svg=svg_path,
        png=png_path,
        width=width,
        height=height,
        is_full=False,
        is_part=True,
        order=order,
        transparent=True,
        suggested_use="PPT 标题块",
    )


def write_manifest(name: str, entries: list[FigurePart], layout: dict[str, dict[str, Any]], generated_at: str) -> None:
    manifest_path = OUT / "manifests" / f"{name}_manifest.json"
    markdown_path = OUT / "manifests" / f"{name}_manifest.md"
    layout_path = OUT / "manifests" / f"{name}_layout.json"
    payload = {
        "generated_at": generated_at,
        "figure": name,
        "asset_root": "reports/figures",
        "counts": {
            "entries": len(entries),
            "parts": len([entry for entry in entries if entry.is_part]),
            "full_figures": len([entry for entry in entries if entry.is_full]),
        },
        "entries": [manifest_entry(entry) for entry in sorted(entries, key=lambda item: item.order)],
        "layout": layout,
        "notes": [
            "所有 parts PNG 均为透明背景，便于 PPT 二次拼装。",
            "整图 PNG 为白色背景，适合直接插入汇报页。",
            "规划阶段模块已标记为规划，避免误导为 fully deployed。",
        ],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_manifest_markdown(name, payload), encoding="utf-8")


def manifest_entry(entry: FigurePart) -> dict[str, Any]:
    return {
        "slug": entry.slug,
        "file_name_svg": rel(entry.svg),
        "file_name_png": rel(entry.png),
        "module_name_zh": entry.zh,
        "module_name_en": entry.en,
        "figure": entry.figure,
        "phase_or_layer": entry.group,
        "suggested_use": entry.suggested_use,
        "is_full": entry.is_full,
        "is_part": entry.is_part,
        "recommended_order": entry.order,
        "width": entry.width,
        "height": entry.height,
        "transparent_background": entry.transparent,
        "description": entry.description,
        "layout": entry.layout,
    }


def render_manifest_markdown(name: str, payload: dict[str, Any]) -> str:
    title = "技术路线图素材清单" if "roadmap" in name else "系统架构图素材清单"
    lines = [
        f"# {title}",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 素材根目录：`{payload['asset_root']}`",
        f"- 整图数量：{payload['counts']['full_figures']}",
        f"- 零件数量：{payload['counts']['parts']}",
        "",
        "| 顺序 | 中文模块名 | 英文名 | 类型 | SVG | PNG | 透明背景 | 用途 |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for entry in payload["entries"]:
        kind = "整图" if entry["is_full"] else "零件"
        lines.append(
            "| {order} | {zh} | {en} | {kind} | `{svg}` | `{png}` | {transparent} | {use} |".format(
                order=entry["recommended_order"],
                zh=entry["module_name_zh"],
                en=entry["module_name_en"],
                kind=kind,
                svg=entry["file_name_svg"],
                png=entry["file_name_png"],
                transparent="是" if entry["transparent_background"] else "否",
                use=entry["suggested_use"],
            )
        )
    lines.extend(
        [
            "",
            "## 拼装说明",
            "",
            "- 优先使用 SVG 进行 PPT 拼装，可保持矢量清晰度。",
            "- 如果 PPT 环境对 SVG 支持不稳定，可使用同名 PNG。",
            "- `*_layout.json` 记录整图中的参考坐标，可用于手工重排。",
            "- 标记为规划的模块表示路线图中的后续增强能力，不应解读为已完全上线。",
            "",
        ]
    )
    return "\n".join(lines)


def write_readme(roadmap: list[FigurePart], architecture: list[FigurePart]) -> None:
    roadmap_parts = len([entry for entry in roadmap if entry.is_part])
    architecture_parts = len([entry for entry in architecture if entry.is_part])
    content = f"""# Figure Asset Package

本目录包含项目可视化图包，优先面向 PPT 拼装，其次兼顾报告和论文汇报插图。

## 整图路径

- 技术路线图 SVG：`reports/figures/roadmap/full/technology_roadmap_full.svg`
- 技术路线图 PNG：`reports/figures/roadmap/full/technology_roadmap_full.png`
- 系统架构图 SVG：`reports/figures/architecture/full/system_architecture_full.svg`
- 系统架构图 PNG：`reports/figures/architecture/full/system_architecture_full.png`

## 零件素材路径

- 技术路线图零件：`reports/figures/roadmap/parts/`
- 系统架构图零件：`reports/figures/architecture/parts/`
- 技术路线图零件数量：{roadmap_parts} 个组件，每个组件同时提供 SVG 和 PNG
- 系统架构图零件数量：{architecture_parts} 个组件，每个组件同时提供 SVG 和 PNG

所有零件 PNG 均使用透明背景，适合在 PPT 中自由拖拽、缩放、重排。

## Manifest 文件

- `reports/figures/manifests/technology_roadmap_manifest.json`
- `reports/figures/manifests/technology_roadmap_manifest.md`
- `reports/figures/manifests/technology_roadmap_layout.json`
- `reports/figures/manifests/system_architecture_manifest.json`
- `reports/figures/manifests/system_architecture_manifest.md`
- `reports/figures/manifests/system_architecture_layout.json`

manifest 记录文件路径、模块中英文名、所属阶段或层级、建议用途、宽高、是否透明背景和推荐放置顺序。
layout JSON 记录整图中各模块的大致坐标，可用于后续在 PPT 中手工拼装参考。

## 预览图

- `reports/figures/previews/roadmap_preview.png`
- `reports/figures/previews/architecture_preview.png`

## PPT 拼装建议

1. 需要整体展示时，直接插入 full 目录下的 SVG 或 PNG。
2. 需要分步讲解时，插入 parts 目录下的模块卡片、容器和箭头。
3. 推荐使用 SVG 做主素材；需要最大兼容性时使用 PNG。
4. 可按 manifest 中的 recommended_order 逐步出现模块，形成动画讲解。
5. 需要重新排版时，参考 layout JSON 中的 x/y/width/height。

## 当前能力与规划能力说明

- 当前已实现或已有本地 MVP 的能力包括：Source Registry、Access Guardrails、crawler mock/connector fixture、Cleaner Agent、Feature Builder、Predictor Agent、Feedback、Experience Bank、Minimum Data Analyzer、FastAPI、Dashboard、Worker/Scheduler、本地 smoke test、Source Discovery fixture/mock 集成等。
- 技术路线图中的里程碑二，以及里程碑三中的部分自动化模型部署、自动文献/数据库检索、自动模型库扩展、自动特征选择和完整迭代优化，属于下一阶段规划或研究推进能力。
- 架构图同时包含当前实现能力和规划中的增强能力；不应误导为所有高级能力均已 fully deployed。
- 任何真实数据接入、live search、live fetch、模型上线或生产替换都需要经过人工复核和 Live Access Guardrails。
"""
    (OUT / "FIGURE_ASSET_README.md").write_text(content, encoding="utf-8")


def verify_assets() -> dict[str, Any]:
    required = [
        OUT / "roadmap" / "full" / "technology_roadmap_full.svg",
        OUT / "roadmap" / "full" / "technology_roadmap_full.png",
        OUT / "architecture" / "full" / "system_architecture_full.svg",
        OUT / "architecture" / "full" / "system_architecture_full.png",
        OUT / "manifests" / "technology_roadmap_manifest.json",
        OUT / "manifests" / "system_architecture_manifest.json",
        OUT / "previews" / "roadmap_preview.png",
        OUT / "previews" / "architecture_preview.png",
        OUT / "FIGURE_ASSET_README.md",
    ]
    missing = [rel(path) for path in required if not path.exists()]
    roadmap_parts = sorted((OUT / "roadmap" / "parts").glob("*.png"))
    architecture_parts = sorted((OUT / "architecture" / "parts").glob("*.png"))
    transparent_checks = []
    for path in roadmap_parts[:3] + architecture_parts[:3]:
        with Image.open(path) as image:
            transparent_checks.append(image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0)
    return {
        "missing": missing,
        "roadmap_png_parts": len(roadmap_parts),
        "architecture_png_parts": len(architecture_parts),
        "transparent_sample_ok": all(transparent_checks),
    }


class SvgCanvas:
    def __init__(self, width: int, height: int, *, background: str | None) -> None:
        self.width = width
        self.height = height
        self.background = background
        self.body: list[str] = []

    def defs(self) -> None:
        self.body.append(
            """
<defs>
  <marker id="arrowhead" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto">
    <path d="M 0 0 L 10 4 L 0 8 z" fill="context-stroke"/>
  </marker>
</defs>
""".strip()
        )

    def round_rect(self, x: float, y: float, w: float, h: float, r: float, fill: str, stroke: str, sw: float) -> None:
        self.body.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def circle(self, cx: float, cy: float, r: float, fill: str) -> None:
        self.body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"/>')

    def text(self, x: float, y: float, text: str, size: int, fill: str, *, weight: str = "normal", anchor: str = "start") -> None:
        self.body.append(
            f'<text x="{x}" y="{y}" font-family="Microsoft YaHei, SimHei, Arial, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(text)}</text>'
        )

    def text_block(self, x: float, y: float, width: float, text: str, size: int, fill: str, *, max_chars: int, weight: str = "normal") -> None:
        lines = wrap_chars(text, max_chars=max_chars)[:3]
        for idx, line in enumerate(lines):
            self.text(x, y + idx * (size + 6), line, size, fill, weight=weight)

    def line_arrow(self, x1: float, y1: float, x2: float, y2: float, color: str, sw: float) -> None:
        self.body.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="round" marker-end="url(#arrowhead)"/>'
        )

    def path(self, d: str, color: str, sw: float) -> None:
        self.body.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-linecap="round" marker-end="url(#arrowhead)"/>'
        )

    def render(self) -> str:
        bg = ""
        if self.background is not None:
            bg = f'<rect width="100%" height="100%" fill="{self.background}"/>'
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">\n{bg}\n'
            + "\n".join(self.body)
            + "\n</svg>\n"
        )


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold and FONT_BOLD.exists() else FONT_REGULAR
    if not path.exists():
        path = FALLBACK_FONT
    return ImageFont.truetype(str(path), size=size)


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    text_font: ImageFont.FreeTypeFont,
    fill: str,
    *,
    max_width: int,
    line_spacing: int = 4,
) -> None:
    x, y = xy
    lines = wrap_pixels(draw, text, text_font, max_width)[:3]
    for line in lines:
        draw.text((x, y), line, fill=fill, font=text_font)
        bbox = draw.textbbox((x, y), line, font=text_font)
        y += (bbox[3] - bbox[1]) + line_spacing


def draw_center_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    text_font: ImageFont.FreeTypeFont,
    fill: str,
    *,
    width: int,
) -> None:
    x, y = xy
    lines = wrap_pixels(draw, text, text_font, width)[:2]
    line_height = text_font.size + 6
    total = len(lines) * line_height
    for idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=text_font)
        draw.text((x - (bbox[2] - bbox[0]) / 2, y + idx * line_height - total / 2), line, fill=fill, font=text_font)


def draw_arrow_png(draw: ImageDraw.ImageDraw, x1: float, y1: float, x2: float, y2: float, color: str) -> None:
    draw.line([x1, y1, x2, y2], fill=color, width=5)
    angle = math.atan2(y2 - y1, x2 - x1)
    arrow_len = 18
    points = [
        (x2, y2),
        (x2 - arrow_len * math.cos(angle - 0.45), y2 - arrow_len * math.sin(angle - 0.45)),
        (x2 - arrow_len * math.cos(angle + 0.45), y2 - arrow_len * math.sin(angle + 0.45)),
    ]
    draw.polygon(points, fill=color)


def draw_arc_arrow(draw: ImageDraw.ImageDraw, color: str) -> None:
    points = [(34, 84), (90, 50), (150, 35), (220, 38), (282, 55), (322, 72)]
    draw.line(points, fill=color, width=5, joint="curve")
    draw.polygon([(322, 72), (300, 68), (311, 52)], fill=color)


def wrap_pixels(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    if " " in text and text.isascii():
        return wrap_words(draw, text, text_font, max_width)
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        bbox = draw.textbbox((0, 0), trial, font=text_font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def wrap_words(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = word if not current else f"{current} {word}"
        bbox = draw.textbbox((0, 0), trial, font=text_font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def wrap_chars(text: str, *, max_chars: int) -> list[str]:
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)] or [""]


def layout_item(x: float, y: float, width: float, height: float, z_index: int) -> dict[str, Any]:
    return {"x": round(x, 2), "y": round(y, 2), "width": round(width, 2), "height": round(height, 2), "z_index": z_index}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
