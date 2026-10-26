# WHO 官方 fluID Fresh 路径优化最终报告

## 最终状态

FRESH_THREE_OFFICIAL_TRIALS_COMPLETED_GATES_FAILED_ACTIVE_UNCHANGED

本轮真实新增并消费了三条互不相同、此前未打开的 WHO fluID 末 52 周留出窗口。没有任何一条同时通过全部严格认证门禁，因此未新增 Fresh 认证链路、未部署新页面、未改线上功能。

## 核心结果

| 轨道 | Champion | Fresh WAPE | 吻合度 | 覆盖率 | 最佳基线 WAPE | 结论 |
|---|---|---:|---:|---:|---:|---|
| ZMB SARI rate | seasonal_delta_blend.v1 | 25.96% | 74.04% | 89.74% | 不可认证同样本 | 样本不一致；FAIL |
| LAO ILI rate | seasonal_ensemble.v1 | 33.98% | 66.02% | 87.50% | 32.48% | 分布漂移且落后基线；FAIL |
| ZAF SARI cases | seasonal_delta_blend.v1 | 24.88% | 75.12% | 100.00% | 20.14% | 数值过线但落后季节基线；FAIL |

“吻合度”定义为 max(0, 1-WAPE)×100%，不是分类准确率。ZAF 的 75.12% 不能单独作为认证，因为基线在完全相同的 48 条样本上达到 79.86%。

## 为什么不继续打开更多 Fresh 窗口

1. 已在同一官方数据集中进行了 39 条 rate 序列的无隐藏值筛选、病例数序列的严格筛选，以及 KHM 的额外稳健性筛选。
2. 三次真正 Fresh 打开分别暴露了缺失样本、分布漂移和强季节基线三类不同失败机制。
3. 继续反复打开国家窗口并只报告成功者会形成多重试验/择优报告风险，削弱论文结论。
4. 更合理的下一步是预注册新模型和来源条件，再等待未来从未到达的数据做前瞻评估，而不是复用已消费窗口。

## 安全与非变更

- Trusted observations：366082 → 366082。
- ACTIVE/PUBLISHED forecast runs：6 → 6。
- promotion events：0 → 0。
- active pointer delta：0。
- 线上 HEAD 保持 cc4a1b26786319b315a7480a828bb80b0a0e873c。
- 线上 healthz/readyz 均为 200。
- 隔离分支 HEAD：c780ba5bdfb7afd2b55025f278553a3da4a885bb，未合并、未 push、未部署。
- 相关测试：87 passed，0 failed。

## 论文应如何表述

可表述为：“系统完成了三条全局一次性消费的 Fresh held-out 实验；其中两条在 WAPE、易读吻合度和区间覆盖率上达标，但均未通过相对强基线改进或同样本门禁，因此系统拒绝认证并保持线上不变。”

不得表述为：“已经新增三条 70% 以上 Fresh 正式预测链路”。
