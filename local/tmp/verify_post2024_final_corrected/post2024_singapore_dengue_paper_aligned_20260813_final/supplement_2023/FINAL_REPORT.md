# 2023+ 论文对比实验重跑报告

## 结论

本轮已用 2023 年正式发表的 EWNet 论文替换原 2021 年 Aiken 基准。实验在作者仓库同一份 Singapore dengue 周病例序列、同一 799/26/13 时间切分上重跑。

- 论文 EWNet（作者代码重跑）：WAPE 41.0665%，吻合度 58.93%，RMSE 218.893，MAE 165.182，sMAPE 43.844%。
- 本项目 `lagged_count_boosting.v1`：WAPE 42.4649%，吻合度 57.54%，RMSE 225.131，MAE 170.807，sMAPE 45.904%。
- 项目相对 EWNet 的 WAPE 差距为 3.41%；属于接近而非相差过大，因此没有利用测试集继续调参。
- 该测试窗的最佳方法其实是简单 `last_value`：WAPE 40.0459%。这说明单一 13 周窗口不能证明复杂模型普遍优于基线，也不能以“准确率”单值替代滚动回测。

## 选择依据

选中论文：Panja 等，*Epicasting: An Ensemble Wavelet Neural Network for forecasting epidemics*，Neural Networks 165 (2023)，DOI `10.1016/j.neunet.2023.05.049`。作者仓库同时提供 15 个单变量疫情数据集、EWNet R 源码和逐数据集参数。EWNet 只使用历史病例序列：先以 Haar MODWT 分解，再对各系数序列使用自回归神经网络并汇总预测，符合“单一感染人数时间序列”的公平比较要求。

2024 Singapore dengue 论文未作为主基准：它的 AR/ARIMA/SARIMA 分支虽为单变量，但主 ML/深度学习结果引入气象和搜索趋势，且未找到作者官方代码。2025 Brazil LightGBM 论文需要 27 个州级 InfoDengue 序列；当前正式项目数据只有 53 周国家级 WHO Brazil dengue，无法做同数据复现。

## 可复现性

作者仓库固定提交：`3740776f65c6f4157e67e6a78f71daeade45246b`。输入工作簿 SHA-256：`19b55bfcede7539fd15087f21d0b07e9d2e78dd9b19c6584057fecc721cd325a`；规范 CSV SHA-256：`86f057db4b7e0f56ec5d73b86f90738d2450b92fbd4c1a3109e0cb06e4f025a9`。

作者源码原样 `source()` 会失败：裸 `@export` 造成语法错误，两处 `subset()` 漏传 `subset` 参数。运行副本仅修复这三处运行语法，算法、MODWT、`nnetar`、`p=1` 和 500 repeats 未改变。重跑值与论文表 5 高度一致：RMSE 218.893 vs 218.8，MAE 165.182 vs 165.1，sMAPE 43.844% vs 43.83%，因此可确认复现路径正确。论文未披露随机种子，本轮固定为 20260813，所以不会宣称逐位相同。

## 治理边界

本轮是离线对比实验：数据库写入 0、forecast run 新增 0、promotion 0、active pointer 变化 0、服务重启 0、远端 Git 修改 0。旧 2021 报告保留作历史证据，但不再作为当前主基准。
