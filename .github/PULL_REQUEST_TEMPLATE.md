## 变更说明

<!-- 这个 PR 做了什么，为什么做。 -->

## 类型
- [ ] Bug 修复
- [ ] 新功能
- [ ] 重构 / 性能
- [ ] 文档
- [ ] 构建 / 打包

## 影响范围
<!-- 是否影响统计口径、数据/隐私、安装脚本、LaunchAgent 或打包流程？说明影响。 -->

## 验证
- [ ] `.venv/bin/python -m pytest -q` 通过
- [ ] `cd frontend && npm ci && npm run build && npm audit --omit=dev` 通过
- [ ] 涉及 Swift 的改动：`swiftc scripts/quota_overlay_support.swift tests/quota_overlay_support_tests.swift -o /tmp/t && /tmp/t` 通过

## 隐私自查
- [ ] 未提交 Codex 日志、分析数据库、导出报告、认证数据或含个人路径的截图
