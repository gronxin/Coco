import Foundation

/// Arguments (excluding argv[0]) for the packaged backend binary's `service` subcommand.
func serviceArguments(_ action: String, extra: [String] = []) -> [String] {
    ["service", action] + extra
}

/// Arguments (excluding argv[0]) for installing the overlay from a prebuilt binary.
func overlayInstallArguments(binary: String) -> [String] {
    ["overlay", "install", "--binary", binary]
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

/// Whether the installed service plist already points at our packaged runtime backend.
/// An absent plist, or a legacy source-install (`run.sh`) plist, is not current and
/// therefore needs a (re)install that migrates it.
func serviceArgumentsCurrent(programArguments: [String]?, runtimeBackend: String) -> Bool {
    guard let args = programArguments, let first = args.first else { return false }
    return first == runtimeBackend
}
