#!/usr/bin/env bash
# Build Coco.app and Coco.dmg for macOS distribution.
#
# Usage:
#   scripts/build_macos.sh [--unsigned] [--universal] [--native] [--skip-frontend]
#
#   --unsigned       Skip Developer ID signing and notarization (local smoke build).
#   --universal      Build the Swift binaries fat (arm64 + x86_64). Backend stays
#                    the running Python's arch unless PyInstaller can target universal2.
#   --native         Build for the host arch only (default).
#   --skip-frontend  Reuse an existing frontend/dist instead of rebuilding it.
#
# Env (signing path only):
#   COCO_SIGN_IDENTITY   "Developer ID Application: ..." identity name.
#   NOTARY_PROFILE       keychain profile for `xcrun notarytool`.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="0.1.0"
APP_NAME="Coco.app"
DMG_NAME="Coco.dmg"
BUILD="build"
APP="$BUILD/$APP_NAME"
ICON_BASENAME="Coco"
UNSIGNED=0
UNIVERSAL=0
SKIP_FRONTEND=0

for arg in "$@"; do
  case "$arg" in
    --unsigned) UNSIGNED=1 ;;
    --universal) UNIVERSAL=1 ;;
    --native) UNIVERSAL=0 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

ARCH_FLAGS=()
if [ "$UNIVERSAL" = 1 ]; then
  ARCH_FLAGS=(-arch arm64 -arch x86_64)
fi

# Deployment target must match Info.plist's LSMinimumSystemVersion, otherwise a
# newer default toolchain stamps a higher minimum and older macOS fails to launch.
DEPLOY_TARGET="12.0"
MACHO_TARGETS=(-target "arm64-apple-macos${DEPLOY_TARGET}")
if [ "$UNIVERSAL" = 1 ]; then
  MACHO_TARGETS=(-target "arm64-apple-macos${DEPLOY_TARGET}" -target "x86_64-apple-macos${DEPLOY_TARGET}")
fi

SWIFT_CACHE="/private/tmp/codex-quota-build"

echo "==> 1/7 前端构建"
if [ "$SKIP_FRONTEND" = 0 ]; then
  ( cd frontend && npm ci && npm run build )
else
  [ -f frontend/dist/index.html ] || { echo "缺少 frontend/dist，请先构建或去掉 --skip-frontend" >&2; exit 1; }
fi

echo "==> 2/7 后端打包 (PyInstaller)"
if [ "$UNIVERSAL" = 1 ]; then
  echo "    警告：--universal 仅对 Swift 二进制生效；后端仍为当前 Python 架构（$(uname -m)）。如需 Intel 后端，请改用 universal2 Python 构建。" >&2
fi
.venv/bin/pyinstaller --distpath "$BUILD/dist" --workpath "$BUILD/work" --noconfirm \
  scripts/codex_token_analyzer.spec

echo "==> 3/7 编译额度浮窗"
mkdir -p "$BUILD"
swiftc ${ARCH_FLAGS[@]+"${ARCH_FLAGS[@]}"} ${MACHO_TARGETS[@]+"${MACHO_TARGETS[@]}"} \
  -module-cache-path "$SWIFT_CACHE" -O \
  -o "$BUILD/codex-quota-overlay" \
  scripts/quota_overlay_support.swift scripts/quota_overlay.swift

echo "==> 4/7 编译菜单栏 App"
swiftc ${ARCH_FLAGS[@]+"${ARCH_FLAGS[@]}"} ${MACHO_TARGETS[@]+"${MACHO_TARGETS[@]}"} \
  -module-cache-path "$SWIFT_CACHE" -O -framework AppKit \
  -o "$BUILD/Coco" scripts/macos_app_support.swift scripts/macos_app.swift

echo "==> 5/7 组装 .app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# App icon (from committed master, or regenerate if missing).
if [ ! -f "assets/$ICON_BASENAME.icns" ]; then
  echo "    生成 App 图标"
  swiftc scripts/make_icon.swift -o "$BUILD/make_icon"
  "$BUILD/make_icon" "$BUILD/icon"
  cp "$BUILD/icon/$ICON_BASENAME.icns" "assets/$ICON_BASENAME.icns"
fi
cp "assets/$ICON_BASENAME.icns" "$APP/Contents/Resources/$ICON_BASENAME.icns"
cp "$BUILD/Coco" "$APP/Contents/MacOS/Coco"
ditto "$BUILD/dist/CocoBackend" "$APP/Contents/Resources/backend"
cp "$BUILD/codex-quota-overlay" "$APP/Contents/Resources/codex-quota-overlay"
chmod +x "$APP/Contents/MacOS/Coco" "$APP/Contents/Resources/backend/CocoBackend" \
  "$APP/Contents/Resources/codex-quota-overlay"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>local.codex-token-analyzer</string>
    <key>CFBundleName</key><string>Coco</string>
    <key>CFBundleDisplayName</key><string>Coco Codex管家</string>
    <key>CFBundleIconFile</key><string>Coco</string>
    <key>CFBundleExecutable</key><string>Coco</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppleEventsUsageDescription</key><string>用于打开用量面板与切换额度浮窗</string>
</dict>
</plist>
PLIST

echo "==> 6/7 签名"
if [ "$UNSIGNED" = 1 ]; then
  echo "    --unsigned：跳过 Developer ID 签名，尝试 ad-hoc 签名"
  codesign --force --deep --sign - "$APP" 2>/dev/null || echo "    ad-hoc 签名不可用，保持未签名"
else
  : "${COCO_SIGN_IDENTITY:?签名模式需要设置 COCO_SIGN_IDENTITY，或用 --unsigned}"
  # Sign every nested Mach-O first (the PyInstaller backend ships 60+ .so/.dylib
  # and a Python.framework, all delivered ad-hoc by PyInstaller). Notarization
  # rejects ad-hoc nested code under hardened runtime, so re-sign each with a
  # Developer ID + runtime + timestamp before signing the outer app.
  codesign --force --options runtime --timestamp --sign "$COCO_SIGN_IDENTITY" \
    "$APP/Contents/Resources/codex-quota-overlay"
  find "$APP/Contents/Resources/backend" -type f \
    \( -name '*.so' -o -name '*.dylib' -o -perm -111 \) -print0 \
    | while IFS= read -r -d '' binary; do
        codesign --force --options runtime --timestamp --sign "$COCO_SIGN_IDENTITY" "$binary"
      done
  codesign --force --options runtime --timestamp --sign "$COCO_SIGN_IDENTITY" \
    "$APP/Contents/Resources/backend/CocoBackend"
  codesign --force --options runtime --timestamp \
    --entitlements scripts/Coco.entitlements --sign "$COCO_SIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
fi

echo "==> 7/7 生成 DMG"
STAGE="$BUILD/dmg_stage"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# Finder volume icon for the mounted DMG.
cp "assets/$ICON_BASENAME.icns" "$STAGE/.VolumeIcon.icns"
hdiutil create -volname "Coco Codex管家" -srcfolder "$STAGE" -ov -format UDZO "$BUILD/$DMG_NAME" >/dev/null
# Stamp the volume-icon custom-icon bit (needs a re-master for UDZO).
if SetFile -a C "$BUILD/$DMG_NAME" 2>/dev/null; then
  :
else
  MOUNT_POINT="$BUILD/dmg_mount"
  rm -rf "$MOUNT_POINT"; mkdir -p "$MOUNT_POINT"
  if hdiutil attach "$BUILD/$DMG_NAME" -nobrowse -noautoopen -mountpoint "$MOUNT_POINT" >/dev/null 2>&1; then
    cp "assets/$ICON_BASENAME.icns" "$MOUNT_POINT/.VolumeIcon.icns"
    SetFile -a C "$MOUNT_POINT" 2>/dev/null || true
    sync
    hdiutil detach "$MOUNT_POINT" >/dev/null 2>&1 || true
  fi
fi
if [ "$UNSIGNED" = 0 ]; then
  # Sign the DMG itself so Gatekeeper has a usable signature on the downloaded image.
  codesign --force --timestamp --sign "$COCO_SIGN_IDENTITY" "$BUILD/$DMG_NAME"
fi

if [ "$UNSIGNED" = 0 ] && [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "==> 公证"
  xcrun notarytool submit "$BUILD/$DMG_NAME" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
  xcrun stapler staple "$BUILD/$DMG_NAME"
fi

echo "完成：$APP 与 $BUILD/$DMG_NAME"
