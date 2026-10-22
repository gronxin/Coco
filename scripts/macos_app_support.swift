import Foundation

/// Arguments for the packaged backend binary's `service` subcommand.
func serviceArguments(_ action: String, backend: String, extra: [String] = []) -> [String] {
    [backend, "service", action] + extra
}

/// Arguments for installing the overlay from a prebuilt binary.
func overlayInstallArguments(backend: String, binary: String) -> [String] {
    [backend, "overlay", "install", "--binary", binary]
}

/// Path to a resource shipped inside the app bundle's Contents/Resources.
func bundledResourcePath(appBundlePath: String, name: String) -> String {
    "\(appBundlePath)/Contents/Resources/\(name)"
}

/// Path to the bundled frozen backend executable.
func bundledBackendExecutable(appBundlePath: String) -> String {
    "\(appBundlePath)/Contents/Resources/backend/CocoBackend"
}

/// Whether first-run guidance should be shown.
func welcomeNeeded(hasCodex: Bool, backendRunning: Bool) -> Bool {
    !hasCodex || !backendRunning
}

/// Menu item titles in display order.
func menuTitles(overlayInstalled: Bool, launchAtLogin: Bool) -> [String] {
    [
        "打开面板",
        overlayInstalled ? "隐藏额度浮窗" : "显示额度浮窗",
        "重启本机服务",
        "查看日志",
        launchAtLogin ? "开机自启 ✓" : "开机自启",
        "完全卸载",
    ]
}

/// Directories that may hold the `codex` CLI, most likely first.
func codexCliCandidates(home: String) -> [String] {
    ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.npm-global/bin", "/usr/bin", "/bin"]
}
