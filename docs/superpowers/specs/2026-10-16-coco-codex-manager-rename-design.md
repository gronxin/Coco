# Coco Codex管家产品改名设计

## 背景

当前产品对外名称为“Codex Token 账本”。本次将对外展示名称统一改为“Coco Codex管家”，不改变产品功能、数据格式或内部技术标识。

## 命名规则

- 标准产品名称：`Coco Codex管家`。
- Web 侧边栏沿用现有两行品牌布局：首行显示 `Coco`，次行显示 `Codex管家`。
- 浏览器标题、README、主 PRD、许可证、API 元数据、导出报告和任务队列对外标题使用完整标准名称。
- 普通功能文案中的 Codex、Token、账本等术语按原含义保留，不把所有术语机械替换为产品名。

## 修改范围

1. 更新 Web 侧边栏品牌名称和浏览器页面标题。
2. 更新 FastAPI 对外标题、Markdown 报告标题、任务队列客户端标题及产品自称。
3. 将公开下载文件名改为 `coco-codex-manager-snapshot.json`、`coco-codex-manager-report.json` 和 `coco-codex-manager-report.md`。
4. 更新 README、主 PRD 和许可证中的产品名称或贡献者归属名称。
5. 更新直接断言对外名称和下载文件名的自动化测试。

JSON 导出不增加字段，也不修改现有字段。`format`、`version` 和其余数据结构保持不变。

## 兼容性边界

以下内部技术标识保持不变：

- 仓库和 Python 包名称 `codex-token-analyzer`。
- npm 包名称 `codex-token-analyzer-ui`。
- LaunchAgent 标识 `local.codex-token-analyzer.service` 和 `local.codex-token-analyzer.overlay`。
- Application Support 数据目录 `~/Library/Application Support/CodexTokenAnalyzer`。
- 默认数据库路径 `.data/analytics.sqlite`。
- 环境变量 `TOKEN_ANALYZER_SOURCE`、`TOKEN_ANALYZER_DB`、`TOKEN_ANALYZER_PRIVACY_MODE` 和 `CODEX_ANALYZER_MANAGED`。
- 本地存储键 `codex-token-analyzer-theme` 和 `task-columns`。
- app-server 客户端机器标识 `codex-token-analyzer` 和 `codex-token-analyzer-queue`。
- 队列请求服务标识 `codex_token_analyzer_queue`。
- JSON 数据格式标识 `codex-token-snapshot` 和 `codex-token-weekly-report`。
- `docs/superpowers/**`、`docs/implementation-plan.md`、`docs/task-queue-design.md` 和 Git 提交记录中的历史名称。

保留这些标识可以避免现有安装升级后丢失服务配置、主题偏好或分析数据。

## 验收

- Web 侧边栏显示两行品牌：`Coco` / `Codex管家`。
- 浏览器标签标题显示 `Coco Codex管家`。
- OpenAPI 标题为 `Coco Codex管家`，Markdown 报告标题为 `Coco Codex管家用量报告`。
- 任务队列 `clientInfo.title` 为 `Coco Codex管家任务队列`，桥接指令中的产品自称为 `Coco Codex管家`。
- 三个 JSON/Markdown 下载文件名使用 `coco-codex-manager-*`，JSON 格式标识和结构保持不变。
- README、主 PRD 和许可证使用新产品名称。
- 内部技术标识未发生变化。
- Python 自动化测试精确覆盖 OpenAPI 标题、报告标题、下载文件名、JSON 格式标识、`version`、两类 JSON 的完整顶层键集合、任务队列对外标题和保留的 `codex_token_analyzer_queue` 服务标识。
- 前端构建通过，并通过浏览器检查侧边栏品牌和页面标题。
- 对当前产品文件扫描旧产品自称，不再出现 `Codex Token 账本`、`Codex Token 本地账本`、`Codex Token 用量报告`、`Codex Token 任务队列`、`Codex Token 分析器` 或 `Codex Token Analyzer contributors`。本规格用于描述迁移来源的引用和上文列出的历史路径不参与该断言。
