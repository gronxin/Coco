import Foundation

private func eq<T: Equatable>(_ a: T, _ b: T, _ m: String) {
    guard a == b else { fatalError("\(m): expected \(b), got \(a)") }
}

@main
private struct MacAppSupportTests {
    static func main() {
        eq(serviceArguments("install", extra: ["--backend", "/src"]),
           ["service", "install", "--backend", "/src"], "install args")
        eq(serviceArguments("status"), ["service", "status"], "status args")
        eq(overlayInstallArguments(binary: "/r/ov"),
           ["overlay", "install", "--binary", "/r/ov"], "overlay args")
        eq(bundledResourcePath(appBundlePath: "/A/Coco.app", name: "codex-quota-overlay"),
           "/A/Coco.app/Contents/Resources/codex-quota-overlay", "resource path")
        eq(bundledBackendExecutable(appBundlePath: "/A/Coco.app"),
           "/A/Coco.app/Contents/Resources/backend/CocoBackend", "backend exe")
        eq(welcomeNeeded(hasCodex: false, backendRunning: true), true, "welcome no codex")
        eq(welcomeNeeded(hasCodex: true, backendRunning: false), true, "welcome no backend")
        eq(welcomeNeeded(hasCodex: true, backendRunning: true), false, "no welcome")
        eq(menuTitles(overlayInstalled: false, launchAtLogin: true),
           ["打开面板", "显示额度浮窗", "重启本机服务", "查看日志", "开机自启 ✓", "完全卸载"],
           "menu titles")
        eq(menuTitles(overlayInstalled: true, launchAtLogin: false),
           ["打开面板", "隐藏额度浮窗", "重启本机服务", "查看日志", "开机自启", "完全卸载"],
           "menu titles hidden")
        let cands = codexCliCandidates(home: "/Users/x")
        eq(cands.contains("/opt/homebrew/bin"), true, "brew")
        eq(cands.contains("/usr/local/bin"), true, "usr local")
        eq(cands.contains("/Users/x/.npm-global/bin"), true, "npm global")
        // A LaunchAgent plist pointing at our runtime backend is "current".
        eq(serviceArgumentsCurrent(programArguments: ["/rt/backend/CocoBackend", "serve"],
                                   runtimeBackend: "/rt/backend/CocoBackend"), true, "current")
        // A legacy source-install (run.sh) or missing plist is not current → must reinstall.
        eq(serviceArgumentsCurrent(programArguments: ["/old/run.sh"],
                                   runtimeBackend: "/rt/backend/CocoBackend"), false, "legacy")
        eq(serviceArgumentsCurrent(programArguments: nil,
                                   runtimeBackend: "/x"), false, "absent")
        print("macos app support tests passed")
    }
}
