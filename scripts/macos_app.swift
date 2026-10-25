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

private var runtimeBackendExecutable: String {
    FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/CodexTokenAnalyzer/backend/CocoBackend")
        .path
}

/// Run an executable. `arguments` excludes argv[0] (Process supplies it via executableURL).
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

    private var servicePlistPath: String {
        launchAgentsDir.appendingPathComponent("\(serviceLabel).plist").path
    }

    private var overlayPlistPath: String {
        launchAgentsDir.appendingPathComponent("\(overlayLabel).plist").path
    }

    private var servicePlist: [String: Any]? {
        NSDictionary(contentsOfFile: servicePlistPath) as? [String: Any]
    }

    private var overlayInstalled: Bool {
        FileManager.default.fileExists(atPath: overlayPlistPath)
    }

    private var launchAtLogin: Bool {
        (servicePlist?["RunAtLoad"] as? Bool) ?? false
    }

    /// Whether the installed LaunchAgent already points at our packaged runtime backend.
    private var serviceCurrent: Bool {
        let args = servicePlist?["ProgramArguments"] as? [String]
        return serviceArgumentsCurrent(programArguments: args, runtimeBackend: runtimeBackendExecutable)
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
        if let icon = NSApplication.shared.applicationIconImage {
            let menuIcon = (icon.copy() as? NSImage) ?? icon
            menuIcon.size = NSSize(width: 18, height: 18)
            menuIcon.isTemplate = false
            statusItem.button?.image = menuIcon
        } else {
            statusItem.button?.title = "Coco"
        }
        rebuildMenu()
        bootstrapIfNeeded()
        openPanelWhenReady()
        if welcomeNeeded(hasCodex: hasCodex, backendRunning: backendRunning) {
            showWelcome()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    /// Ensure our packaged service is installed and current, migrating any legacy
    /// source-install (the install path boots out the same Label safely).
    private func bootstrapIfNeeded() {
        guard FileManager.default.isExecutableFile(atPath: backendExecutable) else { return }
        if !serviceCurrent {
            _ = runBackend(serviceArguments("install", extra: ["--backend", backendDirectory]))
        }
    }

    @discardableResult
    private func runBackend(_ arguments: [String]) -> Int32 {
        runProcess(backendExecutable, arguments).0
    }

    private func rebuildMenu() {
        let titles = menuTitles(overlayInstalled: overlayInstalled, launchAtLogin: launchAtLogin)
        let selectors: [Selector] = [
            #selector(openPanelAction),
            #selector(toggleOverlayAction),
            #selector(restartServiceAction),
            #selector(openLogsAction),
            #selector(toggleAutostartAction),
            #selector(uninstallAction),
        ]
        let menu = NSMenu()
        for (title, sel) in zip(titles, selectors) {
            let entry = NSMenuItem(title: title, action: sel, keyEquivalent: "")
            entry.target = self
            menu.addItem(entry)
        }
        menu.addItem(NSMenuItem.separator())
        let quit = NSMenuItem(title: "退出", action: #selector(quitAction), keyEquivalent: "")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu
    }

    private func refresh() {
        DispatchQueue.main.async { self.rebuildMenu() }
    }

    // MARK: - Actions

    @objc private func openPanelAction() { openPanelWhenReady() }

    private func openPanelWhenReady() {
        DispatchQueue.global().async {
            for _ in 0..<15 {
                if self.backendRunning { break }
                Thread.sleep(forTimeInterval: 0.3)
            }
            DispatchQueue.main.async { NSWorkspace.shared.open(panelURL) }
        }
    }

    @objc private func toggleOverlayAction() {
        DispatchQueue.global().async {
            if self.overlayInstalled {
                self.runBackend(["overlay", "uninstall"])
            } else {
                self.runBackend(overlayInstallArguments(binary: self.overlayBinary))
            }
            self.refresh()
        }
    }

    @objc private func restartServiceAction() {
        DispatchQueue.global().async {
            self.runBackend(serviceArguments("restart"))
            self.refresh()
        }
    }

    @objc private func openLogsAction() {
        NSWorkspace.shared.open(runtimeDataDir)
    }

    @objc private func toggleAutostartAction() {
        let target = launchAtLogin ? "off" : "on"
        DispatchQueue.global().async {
            self.runBackend(serviceArguments("autostart", extra: [target]))
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
            self.runBackend(serviceArguments("uninstall"))
            self.runBackend(["overlay", "uninstall"])
            if purge {
                self.runBackend(serviceArguments("purge-data"))
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
