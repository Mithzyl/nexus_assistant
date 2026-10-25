import Foundation
import UIKit
import UserNotifications

extension Notification.Name {
    static let commuteOpenAdvisory = Notification.Name("commuteOpenAdvisory")
}

/// APNs 注册与通知点击的唯一入口。
/// 只负责系统权限、token 与 payload 解析；业务状态仍由 CommuteStore 管理。
@MainActor
final class PushNotificationManager: NSObject, ObservableObject {
    static let shared = PushNotificationManager()

    enum RegistrationState: Equatable {
        case idle
        case requesting
        case registered
        case denied
        case unsupported(String)

        var text: String {
            switch self {
            case .idle: return "尚未注册"
            case .requesting: return "正在请求权限"
            case .registered: return "已注册到服务器"
            case .denied: return "系统通知已关闭"
            case .unsupported(let reason): return reason
            }
        }
    }

    @Published private(set) var systemAuthorization: UNAuthorizationStatus = .notDetermined
    @Published private(set) var registration: RegistrationState = .idle
    @Published private(set) var apnsToken: String?
    @Published var lastError: String?

    /// 由 App 注入：token 变化时上传到服务器。
    var onTokenUpload: ((String) -> Void)?

    func refreshAuthorization() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        systemAuthorization = settings.authorizationStatus
    }

    /// 只在用户主动开启提醒时调用；拒绝后不反复弹窗。
    func requestAuthorization() async {
        registration = .requesting
        do {
            let granted = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
            await refreshAuthorization()
            if granted {
                registerForRemote()
            } else {
                registration = .denied
            }
        } catch {
            registration = .unsupported("无法请求通知权限")
            lastError = "系统没有返回通知权限结果。"
        }
    }

    func registerForRemote() {
        guard registration != .requesting else { return }
        UIApplication.shared.registerForRemoteNotifications()
    }

    func handleRegistered(token: String) {
        apnsToken = token
        registration = .registered
        onTokenUpload?(token)
    }

    func handleRegistrationFailure(_ error: Error) {
        // 模拟器或缺少 aps-environment 时会走到这里；准确显示，而不是假装成功。
        registration = .unsupported("当前构建未获得推送令牌")
        lastError = "没有拿到 APNs 令牌：\(error.localizedDescription)"
    }

    var mappedPermission: NotificationPermission {
        switch systemAuthorization {
        case .authorized: return .authorized
        case .provisional: return .provisional
        case .ephemeral: return .ephemeral
        case .denied: return .denied
        default: return .unknown
        }
    }

    /// 系统权限与 App 内开关是两件事，这里只回答系统侧。
    var permissionSummary: String {
        switch systemAuthorization {
        case .authorized: return "系统已允许通知"
        case .provisional: return "系统已允许安静通知"
        case .denied: return "系统已拒绝通知"
        case .notDetermined: return "尚未询问系统权限"
        case .ephemeral: return "临时授权"
        @unknown default: return "未知状态"
        }
    }
}

/// AppDelegate 只做三件事：设置通知代理、接收 APNs token、转发点击。
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in
            PushNotificationManager.shared.handleRegistered(token: token)
        }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in
            PushNotificationManager.shared.handleRegistrationFailure(error)
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        Self.handle(userInfo: response.notification.request.content.userInfo)
    }

    /// 冷启动时系统也会调用这里；解析后交给界面决定是否需要先登录。
    nonisolated static func handle(userInfo: [AnyHashable: Any]) {
        guard let type = userInfo["type"] as? String, type == "commute_advisory" else { return }
        let version = (userInfo["schema_version"] as? NSNumber)?.intValue ?? (userInfo["schema_version"] as? Int)
        guard version == 1 else { return }
        guard let advisoryId = userInfo["advisory_id"] as? String else { return }
        Task { @MainActor in
            NotificationCenter.default.post(name: .commuteOpenAdvisory, object: advisoryId)
        }
    }
}
