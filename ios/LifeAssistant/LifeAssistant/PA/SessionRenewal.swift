import Foundation

/// 滚动续期的客户端契约。后端尚未实现 `POST /api/v1/auth/renew`（见
/// ios/LifeAssistant/docs/session-renewal-contract.md）；远端 404 会被如实标记，
/// 不会伪造续期成功，也不会延长本地期限。
protocol PASessionRenewalClient {
    func renew(using token: String) async throws -> PARenewalResponse
}

struct RemoteSessionRenewalClient: PASessionRenewalClient {
    var client: PAAPIClient

    init(client: PAAPIClient = PAAPIClient()) { self.client = client }

    func renew(using token: String) async throws -> PARenewalResponse {
        var scoped = client
        // 用本次校验过的令牌续期，避免读取到刚被替换的凭据。
        scoped.tokenProvider = { token }
        return try await scoped.renewSession()
    }
}

/// 会话期限属于元数据，不是凭据；令牌本身仍只放在 Keychain。
protocol PASessionMetadataStore {
    func readExpiry() -> Date?
    func saveExpiry(_ date: Date?)
}

struct UserDefaultsSessionMetadata: PASessionMetadataStore {
    private let key = "pa.session.expiresAt"
    private let defaults = UserDefaults.standard

    func readExpiry() -> Date? {
        let interval = defaults.double(forKey: key)
        return interval > 0 ? Date(timeIntervalSince1970: interval) : nil
    }

    func saveExpiry(_ date: Date?) {
        if let date {
            defaults.set(date.timeIntervalSince1970, forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }
}

enum PASessionRenewalState: Equatable {
    case idle
    case renewing
    case renewed(Date)
    /// 后端没有实现续期接口（404/405）；原凭据与期限保持不变。
    case unsupported(String)
    case failed(String)

    var isRenewing: Bool { self == .renewing }
}
