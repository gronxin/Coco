import AppKit
import Foundation

private let panelURL = URL(string: "http://127.0.0.1:8765")!
private let serviceLabel = "local.codex-token-analyzer.service"
private let overlayLabel = "local.codex-token-analyzer.overlay"

private var launchAgentsDir: URL {
    FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/LaunchAgents")
}

private var runtimeDataDir: URL {
    FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/CodexTokenAnalyzer/data")
}

private func runProcess(_ executable: String, _ arguments: [String]) -> (Int32, String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    do {
        try process.run()
    } catch {
        return (-1, "")
    }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let appBundlePath = Bundle.main.bundlePath

    private var backendExecutable: String { bundledBackendExecutable(appBundlePath: appBundlePath) }
    private var backendDirectory: String { "\(appBundlePath)/Contents/Resources/backend" }
    private var overlayBinary: String { bundledResourcePath(appBundlePath: appBundlePath, name: "codex-quota-overlay") }

    private var overlayInstalled: Bool {
        FileManager.default.fileExists(atPath:
            launchAgentsDir.appendingPathComponent("\(overlayLabel).plist").path)
    }

    private var launchAtLogin: Bool {
        let path = launchAgentsDir.appendingPathComponent("\(serviceLabel).plist").path
        guard let dict = NSDictionary(contentsOfFile: path) else { return false }
        return (dict["RunAtLoad"] as? Bool) ?? false
    }

    private var backendRunning: Bool {
        let result = runProcess("/usr/bin/curl", ["-s", "-o", "/dev/null",
                                                  "-w", "%{http_code}",
                                                  "http://127.0.0.1:8765/api/service/status"])
        return result.1.trimmingCharacters(in: .whitespacesAndNewlines) == "200"
    }

    private var hasCodex: Bool {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for dir in codexCliCandidates(home: home) {
            if FileManager.default.isExecutableFile(atPath: "\(dir)/codex") { return true }
        }
        for app in ["/Applications/ChatGPT.app", "/Applications/Codex.app",
                    "\(home)/Applications/ChatGPT.app", "\(home)/Applications/Codex.app"] {
            if FileManager.default.fileExists(atPath: app) { return true }
        }
        return false
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "Coco"
        rebuildMenu()
        bootstrapIfNeeded()
        openPanel()
        if welcomeNeeded(hasCodex: hasCodex, backendRunning: backendRunning) {
            showWelcome()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    private func bootstrapIfNeeded() {
        guard FileManager.default.isExecutableFile(atPath: backendExecutable) else { return }
        if !backendRunning {
            _ = runProcess(backendExecutable,
                           serviceArguments("install", backend: backendExecutable,
                                            extra: ["--backend", backendDirectory]))
        }
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        menu.addItem(item("打开面板", #selector(openPanelAction)))
        menu.addItem(item(overlayInstalled ? "隐藏额度浮窗" : "显示额度浮窗",
                          #selector(toggleOverlayAction)))
        menu.addItem(item("重启本机服务", #selector(restartServiceAction)))
        menu.addItem(item("查看日志", #selector(openLogsAction)))
        menu.addItem(item(launchAtLogin ? "开机自启 ✓" : "开机自启", #selector(toggleAutostartAction)))
        menu.addItem(item("完全卸载", #selector(uninstallAction)))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(item("退出", #selector(quitAction)))
        statusItem.menu = menu
    }

    private func item(_ title: String, _ selector: Selector) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        entry.target = self
        return entry
    }

    private func refresh() {
        DispatchQueue.main.async { self.rebuildMenu() }
    }

    // MARK: - Actions

    @objc private func openPanelAction() { openPanel() }

    private func openPanel() {
        NSWorkspace.shared.open(panelURL)
    }

    @objc private func toggleOverlayAction() {
        DispatchQueue.global().async {
            if self.overlayInstalled {
                _ = runProcess(self.backendExecutable, [self.backendExecutable, "overlay", "uninstall"])
            } else {
                _ = runProcess(self.backendExecutable,
                               overlayInstallArguments(backend: self.backendExecutable,
                                                       binary: self.overlayBinary))
            }
            self.refresh()
        }
    }

    @objc private func restartServiceAction() {
        DispatchQueue.global().async {
            _ = runProcess(self.backendExecutable, serviceArguments("restart", backend: self.backendExecutable))
            self.refresh()
        }
    }

    @objc private func openLogsAction() {
        NSWorkspace.shared.open(runtimeDataDir)
    }

    @objc private func toggleAutostartAction() {
        let target = launchAtLogin ? "off" : "on"
        DispatchQueue.global().async {
            _ = runProcess(self.backendExecutable,
                           serviceArguments("autostart", backend: self.backendExecutable, extra: [target]))
            self.refresh()
        }
    }

    @objc private func uninstallAction() {
        let alert = NSAlert()
        alert.messageText = "完全卸载 Coco Codex管家？"
        alert.informativeText = "将移除后台服务与额度浮窗的登录项。本地数据默认保留。"
        alert.addButton(withTitle: "卸载（保留数据）")
        alert.addButton(withTitle: "卸载并删除本地数据")
        alert.addButton(withTitle: "取消")
        let choice = alert.runModal()
        var purge = false
        if choice == .alertThirdButtonReturn { return }
        if choice == .alertSecondButtonReturn { purge = true }
        DispatchQueue.global().async {
            _ = runProcess(self.backendExecutable, serviceArguments("uninstall", backend: self.backendExecutable))
            _ = runProcess(self.backendExecutable, [self.backendExecutable, "overlay", "uninstall"])
            if purge {
                _ = runProcess(self.backendExecutable, serviceArguments("purge-data", backend: self.backendExecutable))
            }
            self.refresh()
        }
    }

    @objc private func quitAction() {
        NSApp.terminate(nil)
    }

    private func showWelcome() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 200),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "欢迎使用 Coco Codex管家"
        let text = NSTextField(wrappingLabelWithString:
            "首次启动正在准备后台服务。\n\n本工具只读取 ~/.codex 会话日志，不读取认证文件。\n若额度功能不可用，请确认已安装并登录 Codex。")
        text.frame = NSRect(x: 20, y: 60, width: 380, height: 120)
        window.contentView?.addSubview(text)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct CocoApp {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
