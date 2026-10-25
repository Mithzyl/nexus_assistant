import SwiftUI

@main
struct LifeAssistantApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @Environment(\.scenePhase) private var scenePhase

    @StateObject private var state = AppState()
    @StateObject private var commute = CommuteStore()
    @StateObject private var push = PushNotificationManager.shared
    @StateObject private var session = PASessionStore.shared
    @StateObject private var asks = AskStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(state)
                .environmentObject(commute)
                .environmentObject(push)
                .environmentObject(session)
                .environmentObject(asks)
                .tint(Palette.fog)
                .task {
                    push.onTokenUpload = { token in
                        Task { await commute.uploadPushToken(token) }
                    }
                    await push.refreshAuthorization()
                    commute.updatePermission(push.mappedPermission)
                    if push.systemAuthorization == .authorized {
                        push.registerForRemote()
                    }
                    // 冷启动：Keychain 有令牌时先校验身份，再按契约续期。
                    await session.restoreAndRenewIfPossible()
                    DataSourceStore.shared.setAccount(session.accountId)
                    NoteImportStore.shared.setAccount(session.accountId)
                    await asks.bindAccount()
                    commute.setAccount(session.accountId)
                    await commute.load()
                    openPendingAdvisoryIfNeeded()
                }
                .onChange(of: session.state) { _, newState in
                    switch newState {
                    case .signedOut, .expired:
                        asks.clearForLogout()
                        commute.logout()
                        commute.setAccount(nil)
                        DataSourceStore.shared.setAccount(nil)
                        NoteImportStore.shared.setAccount(nil)
                    case .signedIn:
                        DataSourceStore.shared.setAccount(session.accountId)
                        NoteImportStore.shared.setAccount(session.accountId)
                        commute.setAccount(session.accountId)
                        Task {
                            await asks.bindAccount()
                            await commute.load()
                            openPendingAdvisoryIfNeeded()
                        }
                    default:
                        break
                    }
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        asks.resumePolling()
                        Task {
                            // 回前台只走 PASession 这一个续期入口；失败不清凭据。
                            await session.handleForeground()
                            await commute.refreshIfStale()
                            await commute.resumeProgressIfNeeded()
                        }
                    } else {
                        asks.pausePolling()
                        commute.pausePolling()
                        session.handleBackground()
                    }
                }
                .onOpenURL { url in
                    handle(url)
                }
        }
    }

    /// 只读打开一条指定 advisory（通知/验证深链），不触发 checks 或 AI。
    private func openPendingAdvisoryIfNeeded() {
        guard session.isSignedIn, let advisoryId = state.pendingAdvisoryID else { return }
        state.pendingAdvisoryID = nil
        state.open(.commuteAdvisory(advisoryId))
    }

    /// App Intent 与快捷指令复用同一条入口，不另建一套业务调用。
    private func handle(_ url: URL) {
        guard url.scheme == "lifeassistant", url.host == "commute" else { return }
        if url.path == "/refresh" {
            Task { await commute.refresh() }
        } else {
            state.open(.commuteSettings)
        }
    }
}
