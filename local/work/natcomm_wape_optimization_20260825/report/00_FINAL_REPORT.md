# Nature Communications 香港流感对比实验最终报告

## 最终判定

`NATCOMM_HK_FLU_BENCHMARK_COMPLETED_GENERALIZATION_NOT_CONFIRMED`

实验真实完成，但不建议将其写成“项目稳定优于论文模型”。主测试期的知识图谱预测期路由仅小幅优于 AWBE；在 2023–2024 post-COVID 外部测试中没有保持优势。

## 论文与来源

- 论文：An adaptive weight ensemble approach to forecast influenza activity in an irregular seasonality context
- 期刊：Nature Communications 15, 8625 (2024)
- DOI：10.1038/s41467-024-52504-1
- 官方代码/数据提交：`b0bd4ccfd9a4a4183c07f122508056a84d271a0f`
- 官方 Data.zip SHA-256：`27c3003babd3b4ded5cf5c37a670cacba97028062c7ff3b1e24f82777254237c`
- 官方 Results_point.zip SHA-256：`f7c289b81ec3d5cf9b075ca8d8fb903b1da20efba29cba312eaea982454b03de`

## 实验合同

- 开发验证窗口：2006-11-05 至 2007-10-28。
- 主测试：origin 从 2007-11-04 开始，target 截止 2019-07-14。
- 排除论文声明的数据生成机制变化区间：2009-04-05 至 2010-03-21。
- 预测期：0–8 周。
- 主测试严格同配对：4968 行。
- 候选数：450；仅按开发期 RMSE 锁定。
- 测试标签未参与模型或路由选择。
- 作者模型使用仓库发布的逐行预测；没有声称在缺少 R/Windows 环境时完成原码全重跑。

## 项目独立自适应组合

锁定方案：`nnls_w26_hl8_p0`。

| 方法 | RMSE | MAE | WAPE | 吻合度 |
|---|---:|---:|---:|---:|
| 论文常数基线 | 5.9452 | 3.6645 | 75.94% | 24.06% |
| 项目独立自适应组合 | 4.0171 | 2.4255 | 50.27% | 49.73% |
| 论文 AWBE | 2.7472 | 1.6491 | 34.18% | 65.82% |

项目独立组合相对常数基线 RMSE 改善 32.43%，但相对 AWBE 的 RMSE 差 46.23%。因此独立方案没有追平论文方法。

## 知识图谱预测期路由

开发期预先支持的路由是：horizon 0 使用项目组合，horizon 1–8 使用论文 AWBE。该方案是对论文模型的编排扩展，不是独立替代模型。

| 主测试方法 | RMSE | MAE | WAPE | 吻合度 |
|---|---:|---:|---:|---:|
| AWBE | 2.7472 | 1.6491 | 34.18% | 65.82% |
| KG 预测期路由 | 2.7255 | 1.6376 | 33.94% | 66.06% |

主测试期路由相对 AWBE 的 RMSE 改善 0.79%，属于小幅改善。

## post-COVID 外部测试

测试目标日期：2023-03-05 至 2024-01-21，423 个严格配对预测。

| 外部测试方法 | RMSE | MAE | WAPE | 吻合度 |
|---|---:|---:|---:|---:|
| AWBE | 2.1523 | 1.4764 | 48.88% | 51.12% |
| KG 预测期路由 | 2.1571 | 1.4906 | 49.35% | 50.65% |

外部测试中 KG 路由 RMSE 比 AWBE 高 0.22%，WAPE 也略差，因此主期内改善没有获得外部泛化确认。

## 安全与可复现性

- 确定性重放：PASS。
- future-label sentinel：PASS。
- 数据库写入：0。
- forecast run 创建：0。
- Promotion：0。
- Active pointer：0。
- 原项目 tracked dirty 文件未被实验脚本修改。

## 论文使用建议

这篇 Nature Communications 论文可以作为高等级、强复现性的补充基准，但当前结果不适合替换为“项目明显优于论文”的主结果。可以如实写为：知识图谱 horizon-aware 编排在长期主测试期取得 0.79% RMSE 改善，但在 post-COVID 分布变化下未维持该优势，说明路由需要跨时期稳健性约束。
