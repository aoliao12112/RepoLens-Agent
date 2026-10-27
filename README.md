# RepoLens Agent

面向 GitHub 开源项目的技术尽调 Agent。输入仓库地址和使用目标，系统采集可验证的仓库数据，分别分析维护活跃度、工程质量、许可证风险与岗位适配度，最后生成带证据链接的结构化报告。

该项目不依赖本地 GPU。模型通过 API 调用，仓库信息通过 GitHub REST API 获取；没有模型 API Key 时，也可以运行基础仓库数据采集和规则评分。

## 项目定位

RepoLens 不是通用聊天机器人。它解决一个明确问题：在投入时间学习或二次开发前，快速判断一个开源仓库是否活跃、是否容易运行、是否存在许可证或维护风险，以及它是否适合作为个人项目底座。

## 目录结构

- `src/repolens/`：个人实现代码。
- `tests/`：单元测试和端到端测试。
- `docs/FINAL_GOAL.md`：最终功能和验收标准。
- `docs/WEEK1_PLAN.md`：第一周开发安排。
- `docs/IMPLEMENTATION_PLAN.md`：详细修改目标、实施批次和逐批验收标准。
- `docs/UPSTREAM_REFERENCE.md`：参考的开源项目及边界说明。
- `data/evaluation/`：公开仓库评测清单与标注，不存放密钥。

## 安全要求

- 所有 Key 仅从环境变量或本地 `.env` 读取。
- `.env` 不得提交到 Git。
- 日志不得记录完整 Token、请求头或用户简历原文。
- Agent 的结论必须附带 GitHub API 字段或页面链接作为证据。

## 当前状态

已完成批次 0 至批次 8：从 GitHub 数据采集、缓存、规则和 Agent 审查、证据校验，到 FastAPI 任务闭环与 Web 演示页均已打通。页面支持任务进度、分区报告、逐条证据、人工确认，以及 JSON/Markdown 导出。当前进入批次 9，评测指标只从保存的真实运行产物计算，不填写估算结果。

## 本地运行

```powershell
python -m pip install -e ".[dev]"
uvicorn repolens.api:app --reload
```

浏览器打开 `http://127.0.0.1:8000`。不配置 `OPENAI_API_KEY` 时，系统自动使用确定性规则分析；建议配置 `GITHUB_TOKEN` 以提高 GitHub API 限额。

第一次使用请直接阅读 [中文使用指南](docs/USAGE.md)，其中包含 Windows 安装、环境变量、Web 操作、API 示例、评测命令和常见错误处理。

用于简历撰写和面试准备时，可阅读 [项目深挖与面试表达](docs/PROJECT_DEEP_DIVE.md)，其中详细说明个人工作、技术方案、功能效果、验证结果和关键难点。

## API 闭环

- `POST /api/analyses`：创建分析任务。
- `GET /api/analyses/{task_id}`：查询阶段、进度和失败摘要。
- `GET /api/analyses/{task_id}/report`：读取带证据的草稿或最终报告。
- `POST /api/analyses/{task_id}/confirm`：确认目标并接受已审阅风险。
- `GET /health`：检查服务和数据库状态。

## 评测

```powershell
repolens-evaluate --validate-only
repolens-evaluate --output data/evaluation/results/latest
```

规则模式已对清单中的 20 个公开仓库完成真实运行：20/20 生成结构化报告，证据覆盖率为 100%，平均耗时约 10.11 秒。当前人工标注数为 0，因此**不宣称字段准确率**；本轮未调用模型，也不宣称模型费用。详细口径见 [评测说明](docs/EVALUATION.md)，修复过程见 [失败案例](docs/FAILURE_CASES.md)，系统关系见 [架构说明](docs/ARCHITECTURE.md)。

## 已知限制

- 仅支持公开 GitHub 仓库，不克隆或执行第三方代码。
- GitHub Token 未配置或配额耗尽时，任务可能失败或降级。
- 目录树信号只能证明文件存在，不能证明测试真实通过或项目可构建。
- 许可证结论是工程风险提示，不构成法律意见。
- 应用内后台任务适合演示和单机部署，生产环境应替换为独立任务队列。

