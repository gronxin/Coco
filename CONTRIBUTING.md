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

## 构建 macOS 安装包（维护者）

`scripts/build_macos.sh` 串联：前端构建 → PyInstaller 冻结后端 → 编译额度浮窗与菜单栏 App（Swift）→ 组装 `Coco.app` → 签名 → 生成 `Coco.dmg` → 公证与 staple。

本地快速验证打包是否正确（跳过签名与公证，产物仅供自测，不能分发）：

```bash
bash scripts/build_macos.sh --unsigned
```

正式发布需要 Developer ID 签名与公证凭据（仅维护者本机配置，**不要提交到仓库**）：

```bash
# 1) 证书：钥匙串中需有 "Developer ID Application: <你的名字> (<TEAMID>)"
security find-identity -v -p codesigning

# 2) 公证凭据：一次性存入钥匙串 profile（App Store Connect API Key）
xcrun notarytool store-credentials "CocoNotary" \
  --key /path/to/AuthKey_XXXX.p8 --key-id <KEY_ID> --issuer <ISSUER_ID>

# 3) 签名 + 公证发布
COCO_SIGN_IDENTITY="Developer ID Application: <你的名字> (<TEAMID>)" \
NOTARY_PROFILE="CocoNotary" \
bash scripts/build_macos.sh --native
```

- 签名会递归覆盖 PyInstaller 产出的嵌套 `.so`/`.dylib`/framework，并按顺序签 DMG，缺一不可，否则公证被拒。
- `build/`、`*.dmg` 已在 `.gitignore` 中；发布产物上传到 GitHub Releases，不入库。

