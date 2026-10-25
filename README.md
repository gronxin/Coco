# Coco Codex管家

一个非官方、本机运行的 Codex 用量分析工具。它只读采集 Codex 会话日志，按任务、轮次、模型调用和执行流程查看 Token，并提供趋势、异常线索、额度状态、CSV/Markdown/JSON 导出、Standard API 等价费用和订阅费用分摊。

本项目与 OpenAI 没有隶属或授权关系。“Codex”是 OpenAI 的产品名称。

## 功能

- 任务 → 轮次 → 模型调用 → 脱敏执行流程四级下钻。
- 输入、缓存输入、非缓存输入、输出和推理输出分别统计。
- 日历和时段热力图、周期环比、模型/项目/工具排行、异常与效率指标。
- 新旧日志兼容、累计记录核对、请求去重、子代理关联和数据质量状态。
- 模型分时价格、缓存节省、API 等价费用、订阅费用分摊和本地预算提醒。
- Codex 周额度和燃尽预测（依赖本机 `codex app-server`，不可用时保留最后成功值）。
- 任务队列：开启后自动扫描本机未归档主任务的最新轮次；明确因额度耗尽中断的原任务，在额度恢复后先逐一续接，再派发队列新任务。添加任务时可按最近项目选择工作目录，再指定该项目中的任务窗口，把新指令排入原对话的后续轮次；也可新建对话。额度有余量就继续使用，不预留比例；支持队列默认模型、逐任务模型与思考深度及运行记录。功能默认关闭，关闭后当前任务完成，但不再监控和派发。
- 浅色与深色主题、响应式布局和基础无障碍支持。

## 下载安装（macOS）

面向 macOS 的可双击安装包，无需自行准备 Python、Node 或 Xcode。

1. 从 GitHub Releases 下载 `Coco.dmg`。
2. 双击打开，把 `Coco` 拖进「应用程序」。
3. 双击启动（已使用 Developer ID 签名并公证，无安全警告）。

首次启动会：

- 若未检测到 Codex，提示先安装并登录 Codex；
- 把后端服务与额度浮窗注册为当前用户的 LaunchAgent（运行文件与数据库放在 `~/Library/Application Support/CodexTokenAnalyzer`）；
- 自动用默认浏览器打开用量面板。

菜单栏常驻 `Coco` 图标，可打开面板、显示/隐藏额度浮窗、重启本机服务、查看日志、开启/关闭开机自启。退出菜单栏应用后，后端服务与任务队列仍在后台运行。

额度浮窗左侧的导航小图标：左键点击显示/隐藏浮窗；**拖动可移动位置**（记住相对 Codex 窗口的位置）；**右键可选择「退出额度浮窗」**，只关闭浮窗本身（后端服务与菜单栏 App 继续运行），之后可从菜单栏「显示额度浮窗」重新打开。

卸载：菜单栏选择「完全卸载」，会移除两个 LaunchAgent；本地数据默认保留，可再选择同时删除。

## 从源码运行

源码运行需要：Windows、macOS 或 Linux；Python 3.11+；Node.js 20+；已在本机使用 Codex（默认数据目录 `~/.codex`）。若只是使用 macOS 安装包，请忽略本节。

macOS / Linux：

```bash
git clone https://github.com/gronxin/codex-token-analyzer.git
cd codex-token-analyzer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
./run.sh
```

Windows PowerShell：

```powershell
git clone https://github.com/gronxin/codex-token-analyzer.git
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

macOS 上如需服务独立于终端和 Codex 应用持续运行，先停止前台的 `./run.sh`，再执行一次：

```bash
.venv/bin/python scripts/macos_service.py install
```

该命令安装当前用户的 LaunchAgent，登录后自动启动服务，异常退出时自动恢复。为适配 macOS 后台进程对“文稿”目录的访问限制，运行文件及独立数据库放在 `~/Library/Application Support/CodexTokenAnalyzer`。修改源码并构建前端后，执行 `.venv/bin/python scripts/macos_service.py sync` 更新运行文件，再在“任务队列”页面点击“重启本机服务”使更新生效；有任务正在运行时会拒绝重启。可用 `.venv/bin/python scripts/macos_service.py status` 查看状态，或用 `uninstall` 移除服务。任务队列的开启/关闭仍由页面开关控制，与服务是否常驻分开。

macOS 还可安装独立的额度导航按钮：

```bash
.venv/bin/python scripts/macos_overlay.py install
```

切换到 Codex 桌面应用后，按钮显示在左侧导航栏底部并跟随当前 Codex 窗口。点击按钮显示或隐藏额度浮窗；未自定义位置时，浮窗默认位于按钮右侧 8 点，浮窗底边相对按钮底边向上 96 点。任务区域展开时显示待执行总数和前五条任务，点击右侧向上或向下箭头可折叠或展开；点击其他标题、数量、时间戳和空白区域不会打开 Web 页面。点击额度环打开用量总览，点击具体任务打开任务队列。浮窗可拖动，保存的是相对 Codex 窗口的位置。可用 `.venv/bin/python scripts/macos_overlay.py status` 查看状态，或用 `uninstall` 移除。

Windows 10/11 原生桌面可安装额度浮窗预览版。先保持本机 Web 服务运行，再在 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe scripts\windows_overlay.py install
.\.venv\Scripts\python.exe scripts\windows_overlay.py status
```

安装器不请求管理员权限，运行文件写入 `%LOCALAPPDATA%\CocoCodexManager\overlay`，并通过当前用户的 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 登录启动。浮窗仅识别 Windows 原生 Codex 桌面窗口，不支持从 WSL 直接显示。卸载代码和登录启动项时执行 `.\.venv\Scripts\python.exe scripts\windows_overlay.py uninstall`；用户保存的浮窗位置和折叠状态会保留。

Windows 实现已在 macOS 开发环境完成纯逻辑、Win32 假后端、HTTP 生命周期、控制器和安装事务自动化测试，但尚未分别完成 Windows 10 与 Windows 11 原生桌面验收。在真机验收完成前按预览功能使用。

可选环境变量：

| 变量 | 用途 |
|---|---|
| `TOKEN_ANALYZER_SOURCE` | Codex 数据根目录，默认 `~/.codex` |
| `TOKEN_ANALYZER_DB` | 分析数据库路径，默认 `.data/analytics.sqlite` |
| `TOKEN_ANALYZER_PRIVACY_MODE=1` | 将任务标题替换为任务 ID 前缀，并对项目路径生成不可逆短哈希 |

启用隐私模式后应删除旧分析数据库并重新导入，现有数据库中的旧元数据不会被自动改写。

## 数据与隐私

日志采集不会修改 Codex 源文件，系统不会读取或保存认证文件。启用任务队列后，系统会通过本机 Codex app-server 创建新任务，或续接自动发现的额度耗尽原对话；因此选定工作目录中的文件可能被 Codex 任务修改。分析数据库保存：任务标题或其脱敏值、项目路径或其哈希、任务/轮次/请求 ID、模型、Token 指标、时间、源文件路径与行号、脱敏活动摘要、价格、订阅分摊、预算和额度数值。

分析采集不把完整对话、完整命令、工具输出、代码正文、推理正文、系统指令或认证数据写入数据库。**新建任务队列任务是例外：它需要在本地数据库保存你主动填写的完整任务指令、工作目录、模型配置和执行状态**，才能在稍后派发。自动发现和手动托管的已有对话只保存任务 ID、标题、工作目录、模型、思考深度和状态，不复制原提示词；发现过程只读取最新轮次的状态与错误元数据。轮次详情中的请求摘录和原始事件仍按需直接从本机源日志读取。CSV 和报告可能包含任务标题、项目与来源路径，请在分享前检查；需要减少分析元数据暴露时启用隐私模式。删除队列项可删除相应指令记录。

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

真实数据审计读取本机日志；运行前请确认数据目录。详细结果见 [验收记录](docs/verification.md)，贡献方式与 **macOS 安装包构建/签名/公证流程**见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。

## 已知限制

- Codex 日志格式不是稳定的公共数据协议；未知格式会进入诊断，而不是猜测用量。
- 旧格式累计记录不一定能还原为单次模型调用。
- 额度读取依赖本机 Codex CLI/app-server，超时或接口变化时会显示不可用或旧值。Windows 支持 npm 安装产生的 `codex.cmd`/`codex.bat` 启动入口。
- 任务队列要求服务进程持续运行、电脑保持唤醒并联网；额度缺失、模型组合失效或任务需要人工处理时暂停派发。队列开启后每分钟扫描本机未归档的 Codex 主任务；最新轮次具有明确的账户额度耗尽错误时自动纳入优先续接范围，无需预先登记。只要 5 小时和周额度仍有余量便可派发，用尽后等待重置；扫描失败时暂停派发新任务。`resetsAt` 仅用于显示预计时间，续接前重新查询额度；普通 429、网络错误或服务重启后的未确认任务不会自动重发。删除自动发现的项目后，同一失败轮次不会再次加入。
- 多数据源仅合并本机可访问目录，不提供云同步或团队视图。

## 许可证

[MIT](LICENSE)
