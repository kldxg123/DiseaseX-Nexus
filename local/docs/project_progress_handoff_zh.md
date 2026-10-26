# 项目进度与交接说明

## 当前状态

本项目已经形成一个“本地优先”的传染病情报 MVP。当前主链路可以在本地完成：数据源注册、mock/fixture 采集、清洗、特征生成、基线预测、人工反馈、经验库记录、最小数据集合分析、FastAPI 业务接口、Streamlit Dashboard 骨架、Worker/Scheduler 骨架、本地端到端 smoke test，以及 Docker Compose 静态验证。

当前实现保持谨慎边界：默认不访问真实疫情网站，不执行真实远程部署，不自动重训模型，不自动替换生产模型，也不声称预测结果具有保证准确率。预测、风险等级和最小数据集合分析都属于辅助分析，需要人工复核。

## 已完成工作

### 1. 项目基础设施

- 使用 `pyproject.toml` 管理 Python 依赖、包发现和测试配置。
- 已创建 `AGENTS.md`、`README.md`、`.gitignore`、`.dockerignore`、`.env.example`、`Dockerfile`、`docker-compose.yml`。
- Docker Compose 已定义本地服务：Postgres、Redis、MinIO、API、Worker、Dashboard、MLflow。
- `.dockerignore` 已排除服务器信息文件、本地 `.env`、密钥文件、token 文件、本地数据库、缓存、日志和生成物，避免进入 Docker build context。

### 2. 数据库与迁移

- SQLAlchemy ORM 模型集中在 `packages/common/models.py`。
- Alembic 迁移已覆盖当前 MVP 核心表，包括初始 schema、source registry、raw documents、epidemic events、model features、predictions、human feedback、experience bank，以及 feedback 表名收口迁移。
- 已有迁移一致性测试，验证 SQLite 临时库可升级到 head，并与 ORM metadata 保持一致。

### 3. 公共模块

- `packages/common/config.py` 从环境变量读取本地配置。
- `packages/common/db.py` 提供 engine、session、init_db 等数据库工具。
- `packages/common/logging.py` 提供结构化日志和敏感字段掩码。
- `scripts/read_server_info.py` 已实现安全读取器和校验工具，但只用于未来部署准备，不执行连接。

### 4. Crawler 与数据源层

- `crawler_agent` 已包含数据源注册、评分、默认种子源、URL 工具、robots 策略接口、HTTP fetcher、正文抽取、raw document 入库和 crawler MVP。
- GDELT 候选数据源发现模块已支持查询构造、疾病/国家别名扩展、mock 响应解析、候选来源评分和 source_registry 写入。
- 官方/半官方连接器框架已支持 WHO、ECDC、CDC 风格的本地 fixture 解析，并提供稳定 connector 接口。
- 已提供 source seed、source discovery、URL crawl、official connector parse 等 CLI。
- 当前测试均使用 mock、本地 fixture 或临时数据库，不进行真实外网请求。

### 5. Cleaner 与特征工程

- `cleaner_agent` 可以从 raw documents 清洗文本，识别疾病、地点、日期，抽取 epidemic events，并写入事件表。
- Feature Engineering MVP 可以把 epidemic_events 聚合成周粒度 model_features。
- 已有 feature store、feature CLI、本地合成数据和 fixture 测试。

### 6. Predictor Agent

- 已支持三类 baseline：moving average、seasonal naive、feature rule。
- 已实现 rolling-origin backtest、回归/分类指标、预测结果入库和 CLI。
- Human Feedback 与 Experience Bank 已完成，并提供稳定接口：
  - `HumanFeedback`
  - `FeedbackInput`
  - `FeedbackStore`
  - `ExperienceBankStore`
  - `classify_failure_type`
  - `assign_retraining_weight`
  - `build_lesson_json`
- Minimum Data Analyzer MVP 已实现，可基于本地 model_features 和 baseline backtest 评估特征组与历史窗口。
- Model Governance 只做离线 promotion check，不自动晋级模型。

### 7. FastAPI 与 Dashboard

- FastAPI 已提供健康检查、sources、crawl/mock、clean、features、models、feedback、experience、analysis、jobs 等业务接口。
- API 响应已做安全收口，不回显完整 HTML、raw text、完整 feature_json、完整 lesson_json 或敏感配置。
- Streamlit Dashboard 已包含 API client、formatters、components、pages、safety helpers 和 app entrypoint。
- Dashboard 测试通过 mock client 或本地 FastAPI TestClient 运行，不启动真实 Streamlit 服务。

### 8. Worker、Smoke Test 与 Docker 本地检查

- `worker_agent` 已包含 job registry、task definitions、pipeline orchestration、scheduler config、runner 和 CLI。
- `scripts/smoke_test.py` 可使用临时 SQLite 和本地合成数据运行端到端 smoke test。
- `scripts/check_compose.py` 可检查 Compose 核心服务、端口、安全 command 和禁止挂载。
- `scripts/docker_local_check.py` 支持静态检查、可选 build、可选 up、可选容器内 smoke test、可选 down。
- 当前环境 Docker CLI 不可用，因此真实容器启动已安全跳过；静态检查和 mock Docker 命令测试通过。

## 最近验证状态

- Docker 相关本地回归测试通过：`72 passed, 1 warning`。
- `compileall` 已通过 `apps`、`packages`、`scripts`、`migrations`、`tests`。
- Docker static check 已通过。
- 因当前环境没有 Docker CLI，真实 Docker build/up/smoke/down 未执行。
- 当前环境 ruff 不可用，未反复尝试安装。

## 必须持续遵守的约束

- 不读取、不打印、不记录、不提交真实服务器信息文件内容。
- 未经明确批准，不执行 SSH 或远程部署。
- 不把真实 `.env`、密钥、token、服务器信息或凭据放入镜像、日志、测试、文档或 API 响应。
- 未经明确批准，不访问 WHO、ECDC、CDC、GDELT 或任何外部疫情网站。
- 所有真实采集未来必须遵守 robots.txt、站点服务条款、限速、缓存和退避策略。
- 测试不得依赖真实公网、真实密钥、真实服务器或付费/私有数据源。
- 不调用外部 LLM、翻译 API、地理编码 API 或其他在线服务。
- 不改动现有 `src/` 业务代码，除非未来任务明确要求小范围兼容。
- 不声称预测结果保证准确。

## 后续预估难点

### 1. 从 fixture/mock 走向合法真实连接器

当前采集框架已经具备结构，但真实采集是合规和工程风险最高的部分。每个数据源都需要 robots.txt 检查、服务条款确认、限速、缓存策略、User-Agent 策略、失败退避、观测日志，以及默认禁用真实网络访问的安全开关。

### 2. 数据质量与标签可靠性

预测质量依赖事件清洗和标签质量。后续需要增强重复检测、来源冲突处理、质量评分、溯源校验、人工复核流程，以及对低质量来源的降权机制。

### 3. 稀疏与延迟信号下的模型评估

疫情数据通常稀疏、滞后且口径不一致。Baseline 适合作为 MVP，但后续需要明确预测目标、回测窗口、提前量、阈值、置信区间/不确定性口径，以及模型晋级标准。

### 4. Docker Compose 真实集成验证

静态检查已通过，但真实容器环境还没跑通。后续可能遇到依赖安装、镜像拉取、服务健康检查、启动顺序、数据库迁移和容器内 smoke test 等问题。

### 5. 分析员工作流设计

Dashboard/API 已有 MVP，但还需要设计真正适合分析员使用的流程：查看来源覆盖、检查证据链、复核预测、提交反馈、比较模型、理解限制和导出报告。

### 6. 治理、安全与部署边界

后续部署前需要明确 secret 管理、部署目标、备份策略、审计日志、数据保留、权限控制、异常响应和上线审批流程。

## 建议解决方向

1. 在具备 Docker 的本地环境中完成真实 Docker Compose build/up/smoke/down 验证。
2. 在不默认开启真实网络的前提下，补齐连接器 readiness 和合规检查层。
3. 强化 raw documents、epidemic events、model_features 的质量评分与 lineage 摘要。
4. 增强 API 与 Dashboard 的人工复核工作流。
5. 将模型评估门槛、promotion check、人工审批标准固化为策略。
6. 远程部署只从 dry-run 开始，真实执行必须单独明确批准。

## 需要决策的点

- 第一阶段业务范围：哪些国家/地区、疾病、时间跨度和预测 horizon？
- 数据源清单：哪些公开来源经过批准，可以进入真实采集准备？
- 真实采集策略：采集频率、User-Agent、限速、缓存周期、robots.txt 处理方式。
- 人工复核流程：谁可以复核，复核状态有哪些，什么证据足够支持确认？
- 预测目标：病例数预测、暴发风险预测、早期预警，还是来源覆盖监控？
- 模型评价门槛：MAE/RMSE/SMAPE、AUROC/PR-AUC、区间覆盖率等指标如何设阈值？
- 数据保留策略：raw text、HTML、feature、feedback notes、模型结果保留多久？
- 部署目标：本地、内网 VM、云 VM，还是托管容器平台？
- Secret 管理方式：本地 `.env`、平台 secret、Vault，还是其他机制？
- 发布标准：是否要求每次发布前必须通过真实 Docker Compose smoke test？

## 建议新增人员负责的工作包

### 工作包名称

连接器上线准备与合规加固（Live Connector Readiness And Compliance Hardening）

### 工作目标

在不启用真实抓取的前提下，把当前 crawler/connector MVP 扩展为可审计、可配置、默认关闭真实网络访问的连接器 readiness 层，为后续经批准的真实公开数据源采集做准备。

### 背景

当前项目已经有 source registry、mock GDELT discovery、官方 fixture 解析、raw document store 和本地测试。缺少的是一层“上线准备检查”：在任何真实网络请求发生前，先确认每个连接器的合规配置、robots 策略、限速、缓存、退避、parser 和人工审批状态。

### 工作范围

- 新增连接器 readiness 元数据：
  - 数据源名称
  - base URL
  - 允许用途摘要
  - robots policy 状态
  - parser 类型
  - rate limit
  - retry/backoff 策略
  - cache policy
  - live access 是否默认禁用
- 新增 dry-run CLI，例如：
  - `python -m crawler_agent.connector_readiness --source <name> --dry-run`
- 所有测试只使用本地 fixture、mock 或临时数据库。
- 增加校验：没有显式未来批准参数时，拒绝 live fetch。
- README 或 docs 中增加连接器 readiness 与合规 checklist。
- 输出安全 JSON，不包含敏感信息、完整 HTML、密钥、token、服务器信息或私有路径。

### 不在范围内

- 不真实请求 WHO、ECDC、CDC、GDELT 或其他外部网站。
- 不做远程部署。
- 不修改 predictor 行为。
- 不调用外部付费 API、LLM、翻译 API 或地理编码 API。
- 不改动现有 `src/` 业务代码。
- 不做自动模型重训。
- 不做自动模型晋级。

### 预期交付物

- `packages/crawler_agent/connector_readiness.py`
- 可选 CLI 入口或模块执行入口。
- 新增测试：
  - `tests/test_connector_readiness.py`
  - 必要时补强 connector registry 测试。
- 文档更新：连接器 readiness、live access gating、合规 checklist。
- 安全 JSON 输出格式说明。

### 验收标准

- readiness 测试全部通过，且只使用 mock/local 数据。
- 现有 crawler、connector、API、smoke、安全测试继续通过。
- 默认行为不发生真实网络访问。
- 常规测试和本地 smoke test 不能意外触发 live access。
- 输出包含限制说明和人工复核语义。
- 日志、测试输出、API 输出和文档中不出现敏感值、服务器信息、凭据、token、私钥路径或完整 raw 内容。

### 建议测试命令

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_connector_readiness.py tests/test_official_connector_registry.py tests/test_official_connectors.py tests/test_crawler_mvp.py tests/test_api_sources.py tests/test_smoke_test_safety.py tests/test_read_server_info.py
```

### 新成员入手阅读顺序

- `AGENTS.md`
- `README.md`
- `packages/crawler_agent/source_registry.py`
- `packages/crawler_agent/default_sources.py`
- `packages/crawler_agent/connectors/`
- `packages/crawler_agent/http_fetcher.py`
- `packages/crawler_agent/robots.py`
- `tests/test_official_connectors.py`
- `tests/test_crawler_mvp.py`

新成员不得读取或打印真实服务器信息文件，不得执行 SSH，不得在没有单独明确批准的情况下启用真实网络请求。

## 快速交接摘要

| 领域 | 当前状态 | 下一步需要 |
|---|---|---|
| 本地 MVP | 已通过 SQLite/mock/fixture 主链路验证 | 继续保持测试离线、本地优先 |
| Docker Compose | 静态检查通过；当前环境未安装 Docker CLI | 在有 Docker 的机器上跑真实 build/up/smoke/down |
| 真实连接器 | 框架已有，默认禁用真实网络 | 增加 readiness gating 和合规 checklist |
| 预测链路 | baseline、backtest、feedback、experience bank 已完成 | 明确业务指标、阈值和人工审批标准 |
| 部署 | dry-run 已有 | 决定部署目标、secret 管理、备份和审批流程 |
