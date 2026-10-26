# DiseaseX Nexus 私有项目快照

本仓库以 2026-10-26 从服务器读取的项目主体及 Dashboard、来源审核运行目录为主，同时收录本地独有代码和实验资料。原项目工作目录及服务未被修改。

## 内容位置

- `server/epidemic-openclaw/`：服务器现行项目主体源码。
- `server/epidemic-dashboard-runtime/`：现行 Dashboard 源码。
- `server/epidemic-source-review-runtime/`：现行来源审核运行源码。
- `server/aibola/`：服务器侧建模、数据处理与微调相关源码。
- `local/`：本地代码和文档，保留独有实验及历史阶段实现，不自动覆盖服务器版本。
- `snapshot/manifest.jsonl`：全部收入文件的目标路径、SHA-256、脱敏状态和存储位置。
- Releases 中 `snapshot-2026-10-26`：数据、项目微调产物、实验输出及数据库逻辑导出，以去重 blob 方式分卷保存。

代码存入 Git；原始数据、数据库、模型产物不进入 Git 历史，按原项目 AGENTS.md 要求作为私有 Release 附件保存。

## 恢复

先下载本仓库，再将同一 Release 的全部 `*-data-part-*.zip` 放进一个目录。在 Python 3.11 或更高版本运行：

```bash
python restore_snapshot.py --assets /path/to/downloaded-assets --destination /path/to/new-workspace
```

脚本先校验所有附件的 SHA-256，随后将代码和数据恢复到新目录，逐文件验证。目标应使用新建空目录；脚本拒绝覆盖已有文件。

恢复后的业务数据库 SQL 位于 `database/business_database.sql`。在新建测试数据库中按相应迁移与 PostgreSQL 版本恢复；认证账户与运行凭据需自行重新配置。本仓库不会提供实际环境变量、服务器地址、账户密码或密钥。

恢复脚本只重建归档文件，不会启动或部署服务。`USER_HOME`、`SERVER_HOST`、`REDACTED` 等占位符需按新环境配置；数据库内的文件位置和原始材料根目录也需与恢复后的目录对应。

## 快照边界

凭据、实际环境变量、私钥、访问日志、虚拟环境、缓存、未经复核的 UI 截图、历史压缩备份和数据库物理文件不收入。论文 Office/PDF 原件属于出版资料，未作为应用代码及业务数据上传。具体排除清单见 `snapshot/exclusions.json` 和 `snapshot/server-metadata.json`，不能将本仓库描述成操作系统磁盘镜像。

项目自己的微调权重、训练状态及数值模型产物作为附件保留。可重新下载的基础模型不重复上传，其原始文件哈希及恢复线索见 `snapshot/server-metadata.json`。恢复基础模型时须遵守原模型许可并核对哈希。

文本中的检测到的凭据、连接用户信息、服务器地址和用户路径已经占位化。归档过程中采用了语法检查和保守回退，最终合并时保留原始源码文件名；源码检查结果见 `snapshot/source-validation.json`。这份快照完成的是归档与内容校验，不代表执行过完整训练、线上服务启动或预测准确率验证。

数据库逻辑导出使用 pg_dump 的一致性快照，排除认证相关表、所有权和授权信息。运行中的文件在复制前后发生变化时会记录在元数据中，不能宣称这些文件具有跨文件事务一致性。

该系统面向分析人员辅助监测和预测，输出仍需要来源追溯、不确定性说明和人工复核，不保证预测准确或替代医疗判断。
