# 系统架构

```mermaid
flowchart LR
    U[Web / API 用户] --> A[FastAPI 任务接口]
    A --> T[(SQLite 任务与确认记录)]
    A --> S[AnalysisService]
    S --> G[GitHub 采集器]
    G --> C[(TTL 响应缓存)]
    G --> GH[GitHub REST API]
    S --> O[AgentOrchestrator]
    O --> R[确定性规则审查]
    O -. 配置 Key 时 .-> M[OpenAI Agents SDK]
    O --> V[证据校验与风险归并]
    V --> T
    T --> A
```

## 边界设计

- 采集层只负责公开仓库事实，不接受模型生成的数据。
- 规则层提供无模型也可运行的稳定基线。
- 模型层只解释已提供的证据 ID；失败、超时或结构非法时按章节降级。
- 证据校验是最终报告前的质量闸门，无证据的高风险结论必须转为待确认。
- API 任务状态与报告持久化，进程重启不会把遗留任务伪装成成功。

## 关键状态流转

`queued → collecting → reviewing → verifying → awaiting_confirmation → completed`

任一执行阶段均可进入 `failed`，并保存稳定错误码、用户消息和是否可重试标记。
