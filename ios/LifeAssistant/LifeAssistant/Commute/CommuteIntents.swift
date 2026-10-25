import AppIntents
import UIKit

/// 快捷指令复用同一套业务调用：查看走界面，刷新走服务器接口。
struct OpenCommuteIntent: AppIntent {
    static var title: LocalizedStringResource = "查看通勤建议"
    static var description = IntentDescription("打开生活助手里的通勤设置与建议。")
    static var openAppWhenRun: Bool = true

    func perform() async throws -> some IntentResult & ProvidesDialog {
        await MainActor.run {
            if let url = URL(string: "lifeassistant://commute") {
                UIApplication.shared.open(url)
            }
        }
        return .result(dialog: "已打开通勤建议。")
    }
}

struct RefreshCommuteIntent: AppIntent {
    static var title: LocalizedStringResource = "刷新通勤建议"
    static var description = IntentDescription("请求服务器立即检查一次天气和路况。")
    static var openAppWhenRun: Bool = false

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard TokenStore.isSignedIn else {
            return .result(dialog: "需要先在 App 里登录服务器。")
        }
        let repository = RemoteCommuteRepository()
        do {
            let plans = try await repository.listPlans()
            guard let plan = plans.first else {
                return .result(dialog: "还没有保存通勤设置。")
            }
            var current = try await repository.requestCheck(
                planId: plan.id, idempotencyKey: UUID().uuidString, request: nil
            )
            var attempts = 0
            while !current.status.isTerminal && attempts < 3 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                current = try await repository.check(id: current.id)
                attempts += 1
            }
            if current.status == .succeeded,
               let advisoryId = current.advisoryId,
               let advisory = try? await repository.advisory(id: advisoryId),
               let departure = advisory.recommendedDepartureAt {
                return .result(dialog: "建议 \(CommuteFormat.clock(departure)) 出发。")
            }
            // 没有拿到真实结果时，不声称已经生成。
            return .result(dialog: "已向服务器提交刷新，稍后可在 App 查看结果。")
        } catch {
            return .result(dialog: "暂时无法刷新，请稍后再试。")
        }
    }
}

struct CommuteShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: OpenCommuteIntent(),
            phrases: ["查看\(.applicationName)通勤建议", "打开\(.applicationName)通勤"],
            shortTitle: "查看通勤建议",
            systemImageName: "car.fill"
        )
        AppShortcut(
            intent: RefreshCommuteIntent(),
            phrases: ["刷新\(.applicationName)通勤建议"],
            shortTitle: "刷新通勤建议",
            systemImageName: "arrow.clockwise"
        )
    }
}
