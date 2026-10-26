# RepoLens Agent 使用指南

RepoLens 用于分析公开 GitHub 仓库。你提供仓库地址和实际使用目标，系统会采集仓库事实，检查维护状态、工程质量、许可证与目标适配度，最后生成带证据链接的报告。

## 1. 准备环境

需要：

- Python 3.11 或更高版本；
- 可访问 GitHub API 的网络；
- 推荐准备 GitHub Personal Access Token；
- 如需模型增强分析，再准备 OpenAI API Key。

GitHub Token 不是绝对必需，但未认证 API 的限额很低，连续分析多个仓库时很容易触发限流。只分析公开仓库时，不需要给 Token 私有仓库权限。

## 2. 安装

在项目根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果 PowerShell 禁止执行激活脚本，可以直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 3. 配置

复制示例配置：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
GITHUB_TOKEN=你的_GitHub_Token
OPENAI_API_KEY=
OPENAI_MODEL=
```

三种常用配置：

1. **只用规则模式**：填写 `GITHUB_TOKEN`，保留两个 OpenAI 配置为空。
2. **模型增强模式**：同时填写 `OPENAI_API_KEY` 和可用的 `OPENAI_MODEL`。
3. **快速试用**：所有 Key 留空；可以运行，但 GitHub API 很容易限流。

严禁提交 `.env`。项目已经将它加入 `.gitignore`。

## 4. 启动服务

虚拟环境已激活时：

```powershell
uvicorn repolens.api:app --reload --host 127.0.0.1 --port 8000
```

未激活虚拟环境时：

```powershell
.\.venv\Scripts\python.exe -m uvicorn repolens.api:app --reload --host 127.0.0.1 --port 8000
```

看到 `Application startup complete` 后，打开：

- Web 页面：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

使用 `Ctrl+C` 停止服务。

## 5. 使用 Web 页面

### 第一步：填写仓库

输入公开 GitHub 仓库根地址，例如：

```text
https://github.com/openai/openai-agents-python
```

不要填写 Issue、Pull Request、用户主页或搜索页面地址。

### 第二步：写清使用目标

目标越具体，适配分析越有价值。例如：

```text
我想用它开发一个 Python Agent 后端，需要 API、自动化测试、持续集成和清晰许可证，计划一周内完成可演示版本。
```

不要只写“帮我看看这个项目”。这种目标没有明确技术条件，只能得到宽泛结论。

### 第三步：选择分析模式

- 关闭“模型增强”：仅使用确定性规则，不消耗模型 Token，结果稳定可复现。
- 开启“模型增强”：配置有效 OpenAI Key 后，模型会解释已有证据；模型失败时自动回退到规则结果。

### 第四步：阅读报告

任务依次经过：

```text
采集 → 审查 → 证据校验 → 等待确认
```

报告包括：

- 仓库概况和综合建议；
- 维护、工程、许可证和目标适配四个章节；
- 风险严重程度；
- `事实`、`推断`、`待确认`标签；
- GitHub API、仓库文件或网页证据；
- 一周二次开发计划。

评分不能代替证据。遇到高风险项时，先打开证据链接核对，不要只看总分。

### 第五步：人工确认和导出

确认目标描述无误，勾选已经阅读并接受的风险，可填写备注，然后提交确认。报告确认后可以导出：

- JSON：适合程序读取、归档或再次校验；
- Markdown：适合放入项目调研文档或评审记录。

许可证分析只是工程风险提示，不构成法律意见。

## 6. 使用 API

### 创建任务

```powershell
$body = @{
    repository_url = "https://github.com/fastapi/fastapi"
    target = "用于开发带测试和 CI 的 Python API 服务"
    enable_model = $false
} | ConvertTo-Json

$task = Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/analyses" `
    -ContentType "application/json" `
    -Body $body

$task
```

保存返回结果中的 `task_id`：

```powershell
$taskId = $task.task_id
```

### 查询进度

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/analyses/$taskId"
```

当状态变成 `awaiting_confirmation` 后读取报告：

```powershell
$report = Invoke-RestMethod "http://127.0.0.1:8000/api/analyses/$taskId/report"
$report
```

常见任务状态：

- `queued`：已进入队列；
- `collecting`：采集 GitHub 数据；
- `reviewing`：执行规则或模型审查；
- `verifying`：检查证据；
- `awaiting_confirmation`：报告已生成，等待人工确认；
- `completed`：已经确认；
- `failed`：任务失败，查看 `error.code`、`error.message` 和 `error.retryable`。

### 确认报告

先从 `$report.risks` 中选择你已经阅读的 `finding_id`：

```powershell
$confirmation = @{
    target_confirmed = $true
    accepted_risk_ids = @("maintenance.stale_issues")
    notes = "已核对目标和风险证据"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/api/analyses/$taskId/confirm" `
    -ContentType "application/json" `
    -Body $confirmation
```

不能接受的风险不要勾选。确认动作代表你已经阅读风险，不代表系统自动解决了风险。

## 7. 运行测试

```powershell
pytest
ruff check src tests
node --check src/repolens/web/app.js
```

## 8. 运行仓库评测

先校验 20 仓清单：

```powershell
repolens-evaluate --validate-only
```

先运行一个案例做烟雾测试：

```powershell
repolens-evaluate --limit 1 --output data/evaluation/results/smoke
```

再执行完整规则评测：

```powershell
repolens-evaluate --output data/evaluation/results/latest
```

逐仓库结果会写入 `data/evaluation/results/`。该目录可能包含第三方 README 正文且体积较大，因此默认不提交 Git。

## 9. 常见问题

### `github_rate_limited`

GitHub API 配额耗尽。配置有效 `GITHUB_TOKEN`，等待配额恢复后重试。不要通过提高并发反复撞限流。

### `repository_unavailable`

检查仓库地址是否正确、仓库是否公开、是否已删除或改名。RepoLens 首版不支持私有仓库。

### 页面一直显示失败

查看页面错误信息和服务端日志中的请求 ID。确认网络、Token、`.env` 和数据库目录可写，然后重新创建任务。

### 开启模型后仍是规则结果

检查 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。模型超时、输出不合法或未配置时，系统会主动降级，这不是静默成功；报告中的 `analysis_mode` 和 `degraded_reviews` 会说明实际模式。

### 数据库在哪里

默认数据库为 `data/repolens.db`。它保存任务、报告、缓存和确认记录。不要在服务运行期间手工修改数据库。

## 10. 推荐演示流程

第一次展示项目时，先关闭模型增强，选择一个公开、活跃且许可证明确的仓库，完整演示“提交—进度—报告—证据—确认—导出”。确认规则模式稳定后，再单独展示模型增强。这样可以证明系统的核心能力不依赖模型碰运气。
