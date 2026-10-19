# Windows 额度浮窗与默认位置上移设计

## 目标

1. 为 Windows 10/11 原生 Codex 桌面应用提供与 macOS 一致的额度导航按钮和浮窗。
2. macOS 与 Windows 的未自定义默认位置统一从导航按钮旁向上移动 96 个逻辑点，避免遮挡升级入口。
3. 两端继续支持拖动，并保存相对 Codex 窗口的位置。

## 范围

- 支持 Windows 10/11 原生桌面环境。
- 支持原生 Python 环境启动浮窗。
- 不支持在 WSL 内直接创建 Windows 浮窗。
- 不修改、不注入 Codex 进程。
- 不在本次任务中新增 Windows Web 服务常驻安装器。

## 平台架构

### macOS

- 保留现有 Swift/AppKit 双 `NSPanel` 和 LaunchAgent。
- 仅修改未自定义时的默认浮窗锚点。
- 现有 `UserDefaults`、拖动位置和安装方式保持兼容。

### Windows

新增三个模块：

- `scripts/windows_overlay_support.py`
  - 纯逻辑，不依赖 Windows GUI。
  - 负责缩放、锚点、屏幕钳制、点击命中、拖动阈值、队列过滤和状态校验。
- `scripts/windows_quota_overlay.py`
  - Tkinter 运行时。
  - 通过 `ctypes` 调用 Win32 API，负责前台窗口识别、双浮窗绘制、跟随、交互和数据刷新。
- `scripts/windows_overlay.py`
  - 提供 `install | status | uninstall`。
  - 将运行文件复制到当前用户的本地应用数据目录，并注册当前用户登录启动。

Windows 实现只使用 Python 标准库，不新增运行时依赖。

## Codex 窗口识别

- 每 100 毫秒读取 Windows 前台顶层窗口。
- 窗口必须可见、未最小化且尺寸至少为 120×120 逻辑像素。
- 进程名为 `Codex.exe` 时直接接受。
- 进程名为 `ChatGPT.exe` 时，仅在窗口标题包含 `Codex` 时接受。
- 未识别到有效 Codex 窗口时隐藏导航按钮和浮窗，但不修改用户保存的显示意图。

## DPI 与坐标

- Windows 进程在创建 Tk 根窗口前启用 Per-Monitor DPI Awareness V2。
- 所有产品尺寸以 96 DPI 下的逻辑像素定义，再按目标窗口所在显示器 DPI 缩放。
- Windows 的绝对窗口坐标、显示器工作区和 Win32 定位参数始终使用物理像素；只缩放尺寸和相对偏移，不缩放绝对原点。
- 目标显示器由 `MonitorFromWindow` 选择，DPI 由 `GetDpiForWindow` 读取；逻辑尺寸乘以 `dpi / 96` 后使用 `floor(value + 0.5)` 取整。
- Codex 可见边界优先读取 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`，不可用时回退到 `GetWindowRect`。
- Tk 全局缩放只初始化一次；Canvas 尺寸和负像素字体大小由当前目标 DPI 显式计算，跨屏时重绘，不重复应用 Tk 缩放。
- Windows 使用屏幕左上角物理坐标；macOS 使用 AppKit 左下角坐标。
- 纯逻辑模块分别输出平台所需坐标，运行时不重复计算布局规则。

## 默认锚点

导航按钮保持现有位置：

- 逻辑尺寸 36×36。
- 位于 Codex 窗口左侧内缩 6、底部上移 54。

未自定义浮窗位置：

- 水平方向位于导航按钮右侧 8。
- 浮窗底边在导航按钮底边基础上向上移动 96。
- macOS 使用 `button.minY + 96` 作为浮窗左下角纵坐标。
- Windows 将 8、96 和面板尺寸按目标 DPI 转换为物理长度，再使用 `button.bottom - lift - panelHeight` 计算物理左上角纵坐标。
- 最终位置钳制到目标显示器工作区。

已存在的 macOS 自定义位置不重置。Windows 只有在没有有效自定义位置时使用默认锚点。

## 浮窗交互

- Windows 显示独立的导航按钮和额度浮窗，视觉内容与 macOS 一致。
- 点击导航按钮显示或隐藏浮窗，不切走 Codex。
- 浮窗展示两个额度环、更新时间、任务总数和最多五条任务。
- 任务区右侧箭头是唯一折叠入口。
- 点击额度环打开 Web 用量总览。
- 点击具体任务打开任务队列。
- 点击标题、数量、时间戳和空白区域不执行操作。
- 指针移动不足 4 个逻辑像素仍按点击处理；达到阈值后只执行拖动，不触发点击。
- 保存的左下锚点优先于当前显示坐标；只有展开和折叠两种高度都可完整容纳时，实际浮窗左下角保持不变。
- 靠近工作区顶部等无法同时容纳两种高度时，允许对当前显示位置施加最小临时钳制，但不得改写保存的锚点；空间恢复后回到保存位置。
- 拖动完成后保存两个逻辑偏移：浮窗左边相对 Codex 左边的偏移，以及浮窗底边相对 Codex 顶边的偏移。
- 恢复时先从保存的底边偏移求出物理底边，再减去当前面板高度得到左上角，因此展开、折叠和重启均保持同一底边。
- Codex 移动、缩放、全屏或跨屏时，按钮和浮窗跟随当前前台窗口。

## Windows 窗口行为

- Tkinter 创建一个隐藏根窗口和两个无边框 `Toplevel`。
- 两个窗口设置为置顶、工具窗口、不显示任务栏、不抢焦点。
- 通过透明色和 Canvas 绘制圆角背景、额度环、任务文本和按钮。
- HWND 创建后使用 `GetWindowLongPtr` 读取并合并 `WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`，不得覆盖 Tk 已有扩展样式，也不得设置会造成鼠标穿透的 `WS_EX_TRANSPARENT`。
- 首次显示使用 `ShowWindow(SW_SHOWNOACTIVATE)`；移动、缩放、置顶和再次显示统一使用带 `SWP_NOACTIVATE` 的 `SetWindowPos`。
- 运行时不得调用 `focus_force`、`focus_set` 或其他主动抢焦点 API。
- 点击仍由 Tk 事件处理，但窗口不会成为前台应用。
- `Local\CocoCodexManagerOverlay` 运行互斥量由实例以初始所有者身份取得，并持有所有权和句柄直至退出，保证每个交互登录会话只运行一个实例。
- `Local\CocoCodexManagerOverlayInstall` 安装互斥量由安装器持有整个事务。普通 launcher 发现安装锁被占用时不启动；安装器通过随机 nonce 启动的候选实例可绕过该检查。
- `Local\CocoCodexManagerOverlayStop` 是手动复位命名事件。运行实例持有事件句柄并轮询退出信号。
- 停止旧实例时，安装器读取并校验持久 `instance.json`，通过 PID 打开进程同步句柄，设置退出事件并等待进程句柄变为已退出；不等待、不取得运行互斥量所有权。
- 退出事件在旧进程确认退出后仍保持触发，阻止意外 launcher 进入；文件切换完成后，安装器在直接启动带 nonce 的候选实例前复位事件。安装事务结束时关闭全部事件、进程和互斥量句柄。

## 数据流

- 每 10 秒在后台线程读取：
  - `http://127.0.0.1:8765/api/quota`
  - `http://127.0.0.1:8765/api/night-queue`
- 使用单个后台工作线程串行读取两个接口，每个接口最多一个在途请求，不允许轮次重叠。
- 每个请求连接和读取总超时为 3 秒，响应上限为 1 MiB；超限、畸形 JSON 或字段类型不符均作为本轮失败。
- 网络线程只解析数据；结果通过容量为 1 的 latest-wins 队列交给 Tk 主线程，满时替换旧结果。
- 最多保留五条任务标题，每条截断到 200 个 Unicode 字符；状态和错误文本截断到 300 个字符。
- 额度请求失败时保留最后成功值并显示旧数据状态。
- 队列请求失败时保留最后成功任务数量和标题，并显示不可用状态。
- URL 跳转使用系统默认浏览器。
- 退出事件同时停止轮询和 Tk 主循环；后台线程最多等待 3.5 秒，超时后进程退出。

## Windows 状态

状态文件：

`%LOCALAPPDATA%\CocoCodexManager\overlay-state.json`

字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `overlay_visible` | Bool | `false` | 用户是否希望展示浮窗 |
| `queue_collapsed` | Bool | `false` | 任务区是否折叠 |
| `custom_position` | Bool | `false` | 是否使用拖动位置 |
| `relative_x` | Number/null | `null` | 相对 Codex 窗口左侧的逻辑坐标 |
| `relative_bottom` | Number/null | `null` | 浮窗底边相对 Codex 窗口顶部的逻辑坐标 |

- 状态写入采用临时文件加原子替换。
- 状态文件最大 16 KiB；布尔值必须是严格布尔类型，位置必须成对存在、是有限数值且绝对值不超过 100000，拒绝 `NaN`、`Infinity`、字符串数字和部分坐标。
- JSON 或字段无效时忽略损坏内容并恢复默认值，不覆盖原文件，直到下一次有效用户操作。
- 写入失败时保留原状态文件并继续使用内存状态。
- 临时屏幕钳制不改写保存的相对位置。

## 安装与卸载

运行：

```powershell
.\.venv\Scripts\python.exe scripts\windows_overlay.py install
```

安装行为：

1. 验证当前平台为 Windows，并定位同一虚拟环境的 `pythonw.exe`。
2. 在写入任何文件或注册表前，完整记录旧 HKCU Run 值、launcher 内容、`current.json` 内容、`instance.json` 和已有版本目录。
3. 将新运行文件完整写入 `%LOCALAPPDATA%\CocoCodexManager\overlay\versions\<version>` 临时目录，校验后原子改名为版本目录。
4. 稳定的 `overlay_launcher.pyw` 读取 `current.json` 指针并通过 `runpy` 启动当前版本；启动命令使用绝对路径。
5. 在 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 写入 `CocoCodexManagerOverlay`，直接调用 `pythonw.exe overlay_launcher.pyw`，不创建 `.cmd`，登录时不显示终端窗口。
6. 新版本目录、launcher、指针和注册表共同属于一次安装事务。
7. 按持久 PID + 进程句柄协议请求旧实例退出；超时则安装失败，不强杀未知进程。
8. 原子写入 launcher 和 `current.json`，更新 HKCU Run；生成随机 nonce，通过启动参数传给 launcher 和运行时。
9. 安装器保留直接启动候选进程的进程句柄。新版本原子写入两份数据：`ready-<nonce>.json` 必须匹配候选 PID、版本和 nonce；持久 `instance.json` 保存 PID、版本、nonce 和启动时间，供 `status` 与后续停止使用。
10. 新版本未在 8 秒内就绪时，安装器重新触发退出事件并等待候选进程句柄退出，删除迟到或残留的 nonce ready，再恢复旧 HKCU Run、launcher、指针、`instance.json` 和版本集合，复位事件后重启旧版本。
11. 首次安装失败时执行同样的候选停止流程，并删除本次创建的启动项、launcher、指针、instance、ready 和版本目录，不留下半安装状态。
12. 成功后删除 nonce ready；运行实例退出时仅在 `instance.json` 的 PID 仍等于自身时删除该文件。最多保留当前和上一个版本。
13. 不请求管理员权限。

`status` 返回注册表启动项、当前版本、`instance.json` PID 和进程存活状态。`uninstall` 设置命名退出事件并通过 instance PID 的进程句柄等待退出，删除 HKCU Run 值、launcher、版本、指针和 instance；保留用户状态文件。

## 错误处理

- 非 Windows 执行安装脚本时明确报错，不创建文件。
- 缺少 `pythonw.exe` 时明确提示重新创建 Windows 虚拟环境。
- API 不可用不退出浮窗。
- Codex 未运行或窗口不可用时仅隐藏。
- 显示器断开时钳制到当前工作区，不覆盖自定义偏移。
- 运行文件更新使用版本目录和原子指针切换，失败时回滚并保留旧版本。

## 测试与验收

### 自动化

- macOS Swift 测试增加默认位置上移 96 的断言。
- Windows 纯逻辑测试覆盖：
  - 100%、125%、150% DPI 换算；
  - 默认锚点；
  - 多屏和负坐标钳制；
  - 260×270 与 260×194 面板在“拖动→折叠→重启→展开”中保持同一底边；
  - 靠近工作区顶部时两种高度分别执行最小临时钳制，且不改写保存锚点；
  - 点击命中优先级；
  - 4 像素拖动阈值；
  - 队列过滤；
  - 状态文件大小、严格类型、有限数值、成对坐标和原子写入。
- Win32 适配器使用假 API 测试 Codex 进程名、标题、最小化、扩展边界、前台窗口、物理坐标和混合 DPI 规则。
- 窗口样式测试验证原样式合并、`SWP_NOACTIVATE`、`SW_SHOWNOACTIVATE`，并确认未加入 `WS_EX_TRANSPARENT`。
- 数据线程测试覆盖超时、1 MiB 上限、畸形 JSON、连续失败、latest-wins 和有界退出。
- 安装器使用临时目录和假注册表测试安装锁、运行中升级、nonce/PID/版本 ready 校验、instance 生命周期、候选停止、完整事务回滚、首次失败清理、状态、并发启动、事件复位、卸载和路径引用，不修改真实 HKCU。
- Python 文件在 macOS 上完成语法编译和单元测试。

### Windows 原生验收

必须分别在 Windows 10 和 Windows 11 真机或具有交互桌面的对应环境中，使用真实 Codex 桌面应用验证：

- 登录启动不出现终端窗口。
- Codex 前台时显示导航按钮，后台或最小化时隐藏。
- 按钮和浮窗跟随移动、缩放、全屏及 100%/125%/150% 混合 DPI 跨屏。
- 默认浮窗位于按钮右侧并向上 96 个逻辑像素，不遮挡升级入口。
- 点击按钮、折叠、空白区域和拖动后，`GetForegroundWindow()` 始终仍为 Codex。
- 点击额度环和任务条目单独验证系统默认浏览器打开正确 URL；浏览器是否成为前台不作约束。
- 拖动后执行“折叠→重启→展开”，在两种高度均可容纳时浮窗底边和保存位置一致。
- 将保存位置放到工作区顶部边缘，验证展开和折叠分别只做最小临时钳制，状态文件中的保存锚点不变。
- 并发启动只保留一个实例；运行中升级成功切换版本，模拟失败可回滚。
- 卸载可请求实例退出并移除启动项；损坏状态可恢复默认且不崩溃。
- 显示器断开后浮窗进入当前工作区，重新连接时保存偏移未被覆盖。

当前开发环境是 macOS。只有在 Windows 原生验收完成后，主 PRD 才把 Windows 浮窗列为“已验证产品事实”；本任务内已验证的 macOS 默认位置变化可立即同步。
