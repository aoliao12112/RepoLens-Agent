# 可复现评测说明

## 原则

评测结果必须来自保存的逐仓库 JSON 产物。没有成功运行、没有人工标注或没有模型返回费用时，对应指标保持 `null`，禁止估算或补写。

## 数据集

`data/evaluation/repositories.json` 当前包含 20 个公开仓库候选，覆盖多种语言、Agent 项目、活跃项目、归档项目和内容型仓库。`annotation: null` 表示尚未人工复核，不能用于宣称字段准确率。

人工标注至少包含：

- 归档状态；
- 主要语言；
- 许可证状态；
- 是否存在测试；
- 带时区的标注时间；
- 至少一个公开来源链接。

## 命令

```powershell
repolens-evaluate --validate-only
repolens-evaluate --limit 1 --output data/evaluation/results/smoke
repolens-evaluate --output data/evaluation/results/2026-10-26
```

也可在未安装命令入口时运行：

```powershell
$env:PYTHONPATH = "src"
python -m repolens.evaluation --validate-only
```

建议先配置 `GITHUB_TOKEN`，否则 GitHub 未认证请求很容易触发低配额限制。评测器会保存每个失败项的异常类型和消息，不会静默跳过。

## 指标定义

- `structured_output_rate`：成功生成并通过模型校验的报告数 / 总案例数。
- `evidence_coverage_rate`：除待确认项外，带至少一条证据的发现数 / 对应发现总数。
- `field_accuracy`：人工标注的四个事实字段中，与采集结果完全一致的字段数 / 已标注字段数。
- `average_duration_ms`：所有成功和失败案例的实际墙钟耗时平均值。
- `average_known_cost_usd`：仅对返回真实费用值的模型调用求平均；无观测时为 `null`。

## 当前真实结果

2026-10-26 的规则模式 v2 运行完成 20 个案例：20 成功、0 失败，结构化输出率和证据覆盖率均为 100%，平均墙钟耗时约 10.11 秒。可公开的指标快照位于 `data/evaluation/metrics/2026-10-26-rule-v2.json`，失败与修复过程见 `docs/FAILURE_CASES.md`。

逐仓库原始结果保存在本地 `data/evaluation/results/`，其中含第三方 README 正文且体积较大，已排除在公开 Git 历史之外。人工标注数仍为 0，因此字段准确率是 `null`；本轮未调用模型，因此费用也是 `null`。
