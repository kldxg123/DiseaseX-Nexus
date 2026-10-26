# 标签定义与数据质量约定

本文档描述当前 MVP 的预测标签、目标窗口和数据质量审计规则。它用于工程验证和人工复核，不代表官方病例定义，也不保证预测准确率。

## 当前预测目标

- 粒度：`country` / `admin1` / `disease` / `iso_week`。
- `horizon_days=7`：预测未来 1 周病例数。
- `horizon_days=28`：预测未来 4 周病例数之和。
- `target_outbreak_label`：未来 horizon 内 `outbreaks_count` 总和是否达到 outbreak threshold。

## 时间优先级

- 优先使用 `epidemic_events.event_date`。
- `event_date` 缺失时，可使用 `raw_documents.published_at` 作为人工复核候选。
- 两者都缺失时，该事件不应直接进入训练标签，应先进入人工复核。

## 病例字段规则

- 优先使用 `cases`。
- `cases` 缺失时，可以使用 `confirmed_cases + suspected_cases` 作为 fallback。
- confirmed 与 suspected 的使用必须在 provenance 中记录，供后续审计。

## 暴发标签规则

- `outbreak_threshold` 默认沿用 FeatureBuilder 的配置。
- MVP 默认示例阈值为 3。
- 不同疾病或国家可以调整阈值，但需要记录配置并进行人工复核。

## 数据泄漏边界

- lag / rolling 特征只能使用当前周及之前的数据。
- `target_value` 只能使用未来 horizon 窗口的数据。
- 当未来窗口不完整时，该行应跳过或进入人工复核，不应静默进入训练。
- `feature_json` 中不应出现 `future`、`next_`、`target`、`label` 等暗示未来标签的字段。

## 重要限制

- 当前标签定义服务于工程 MVP。
- 它不等同于官方流行病学病例定义或暴发定义。
- 真实使用前需要公共卫生专家和数据分析人员共同复核。
