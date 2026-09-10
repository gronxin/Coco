# 贡献指南

使用 Python 3.11+ 和 Node.js 20+。按 README 完成安装，修改前先运行现有测试。跨平台改动由 GitHub Actions 在 Ubuntu 和 Windows 上验证。

1. 不提交 Codex 日志、分析数据库、导出报告、认证数据或包含个人路径的截图。
2. 新日志格式必须提供程序生成或人工脱敏的测试样本。
3. 用量口径修改必须覆盖去重、累计记录、未知字段和汇总一致性。
4. UI 修改应检查浅色/深色主题及 375px、768px 和桌面宽度。

提交拉取请求前运行：

```bash
.venv/bin/python -m pytest -q
cd frontend && npm ci && npm run build && npm audit --omit=dev
```

请在拉取请求中说明行为变化、统计口径影响、验证结果和隐私影响。
