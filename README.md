# Codex Token 账本

一个非官方、本机运行的 Codex 用量分析工具。它只读采集 Codex 会话日志，按任务、轮次、模型调用和执行流程查看 Token，并提供趋势、异常线索、额度状态、CSV/Markdown/JSON 导出、Standard API 等价费用和订阅费用分摊。

本项目与 OpenAI 没有隶属或授权关系。“Codex”是 OpenAI 的产品名称。

## 功能

- 任务 → 轮次 → 模型调用 → 脱敏执行流程四级下钻。
- 输入、缓存输入、非缓存输入、输出和推理输出分别统计。
- 日历和时段热力图、周期环比、模型/项目/工具排行、异常与效率指标。
- 新旧日志兼容、累计记录核对、请求去重、子代理关联和数据质量状态。
- 模型分时价格、缓存节省、API 等价费用、订阅费用分摊和本地预算提醒。
- Codex 周额度和燃尽预测（依赖本机 `codex app-server`，不可用时保留最后成功值）。
- 浅色与深色主题、响应式布局和基础无障碍支持。

## 运行条件

- Windows、macOS 或 Linux。
- Python 3.11 或更高版本。
- Node.js 20 或更高版本。
- 已在本机使用 Codex，默认数据目录为 `~/.codex`。

## 安装与启动

macOS / Linux：

```bash
git clone <你的仓库地址>
cd codex-token-analyzer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
./run.sh
```

Windows PowerShell：

```powershell
git clone <你的仓库地址>
cd codex-token-analyzer
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
.\run.ps1
```

也可以在命令提示符中执行 `run.cmd`。如果 PowerShell 的本机执行策略禁止运行脚本，使用 `run.cmd`，无需修改系统执行策略。

浏览器打开 <http://127.0.0.1:8765>。服务只绑定本机地址，每 5 秒增量检查一次。首次导入读取 `~/.codex/sessions` 和存在时的 `~/.codex/archived_sessions`；数据目录可在设置页更换。

可选环境变量：

| 变量 | 用途 |
|---|---|
| `TOKEN_ANALYZER_SOURCE` | Codex 数据根目录，默认 `~/.codex` |
| `TOKEN_ANALYZER_DB` | 分析数据库路径，默认 `.data/analytics.sqlite` |
| `TOKEN_ANALYZER_PRIVACY_MODE=1` | 将任务标题替换为任务 ID 前缀，并对项目路径生成不可逆短哈希 |

启用隐私模式后应删除旧分析数据库并重新导入，现有数据库中的旧元数据不会被自动改写。

## 数据与隐私

系统不会修改 Codex 源文件，也不会读取或保存认证文件。分析数据库保存：任务标题或其脱敏值、项目路径或其哈希、任务/轮次/请求 ID、模型、Token 指标、时间、源文件路径与行号、脱敏活动摘要、价格、订阅分摊、预算和额度数值。

系统不把完整对话、完整命令、工具输出、代码正文、推理正文、系统指令或认证数据写入分析数据库。轮次详情中的请求摘录和原始事件按需直接从本机源日志读取。CSV 和报告可能包含任务标题、项目与来源路径，请在分享前检查；需要减少元数据暴露时启用隐私模式。

删除 `.data/analytics.sqlite*` 即可清除分析结果并重新导入，不影响 Codex 日志。不要把 `.data`、导出文件或 Codex 日志提交到 Git。

## 统计与费用口径

- `input_tokens` 已包含缓存输入；`output_tokens` 已包含推理输出，细分值不重复加入总量。
- 新格式按 `response_id` 全局去重。旧格式的可靠 `last_token_usage` 作为历史汇总展示，不伪造请求 ID。
- 无法确定日期的累计差额保留为“时间未知用量”，不进入每日趋势、API 估价和月度订阅分摊。
- Standard API 等价费用依据设置页中的模型、有效期和服务层价格计算，不是 Codex 订阅账单。未知价格不按零元处理。
- 内置价格的来源和核验日期保存在 `analyzer/prices.json`；价格可能变化，发布或分析费用前应核对官方价格。
- 订阅分摊使用上海时区的完整月份和本机已记录总 Token；筛选不改变分母，当月结果为暂估。

## 开发与验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/audit_real_data.py
cd frontend && npm ci && npm run build && npm audit --omit=dev
```

真实数据审计读取本机日志；运行前请确认数据目录。详细结果见 [验收记录](docs/verification.md)，贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。

## 已知限制

- Codex 日志格式不是稳定的公共数据协议；未知格式会进入诊断，而不是猜测用量。
- 旧格式累计记录不一定能还原为单次模型调用。
- 额度读取依赖本机 Codex CLI/app-server，超时或接口变化时会显示不可用或旧值。Windows 支持 npm 安装产生的 `codex.cmd`/`codex.bat` 启动入口。
- 多数据源仅合并本机可访问目录，不提供云同步或团队视图。

## 许可证

[MIT](LICENSE)
