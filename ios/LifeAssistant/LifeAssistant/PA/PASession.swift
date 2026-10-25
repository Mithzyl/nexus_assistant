import Foundation
import Security

// MARK: - Keychain

/// 只保存不透明会话令牌；不写 UserDefaults、日志或源码。
enum PAKeychain {
    private static let service = "com.mith.lifeassistant.pa"
    private static let account = "session-token"
    // A successful read is the source of truth for this process. Repeated
    // SecItemCopyMatching calls can transiently fail while the app relaunches;
    // that must not make a signed-in UI reject a new send as signed out.
    private static var cachedToken: String?
    private static let cacheLock = NSLock()
    private(set) static var lastReadStatus: OSStatus?
    private(set) static var lastWriteStatus: OSStatus?


    static func readToken() -> String? {
        cacheLock.lock()
        defer { cacheLock.unlock() }
        if let cachedToken { return cachedToken }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        lastReadStatus = status
        guard status == errSecSuccess,
              let data = item as? Data,
              let token = String(data: data, encoding: .utf8),
              !token.isEmpty else { return nil }
        cachedToken = token
        return token
    }

    @discardableResult
    static func saveToken(_ token: String) -> Bool {
        let data = Data(token.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]
        let saved: Bool
        let status: OSStatus
        if SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess {
            status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        } else {
            var create = query
            create.merge(attributes) { $1 }
            status = SecItemAdd(create as CFDictionary, nil)
        }
        lastWriteStatus = status
        saved = status == errSecSuccess
        if saved {
            cacheLock.lock()
            cachedToken = token
            cacheLock.unlock()
        }
        return saved
    }

    /// Diagnostic read that bypasses the in-process cache. Never returns the token
    /// in logs; callers can compare a boolean or status without exposing it.
    static func readTokenDirect() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        lastReadStatus = status
        guard status == errSecSuccess,
              let data = item as? Data,
              let token = String(data: data, encoding: .utf8),
              !token.isEmpty else { return nil }
        return token
    }

    static func deleteToken() {
        cacheLock.lock()
        cachedToken = nil
        cacheLock.unlock()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(query as CFDictionary)
    }
}

// MARK: - 令牌存储

protocol PATokenStorage {
    func read() -> String?
    @discardableResult func save(_ token: String) -> Bool
    func delete()
}

struct KeychainTokenStorage: PATokenStorage {
    func read() -> String? { PAKeychain.readToken() }
    @discardableResult func save(_ token: String) -> Bool { PAKeychain.saveToken(token) }
    func delete() { PAKeychain.deleteToken() }
}

/// 仅测试使用；生产始终用 Keychain。
final class MemoryTokenStorage: PATokenStorage {
    private var token: String?
    init(token: String? = nil) { self.token = token }
    func read() -> String? { token }
    @discardableResult func save(_ token: String) -> Bool { self.token = token; return true }
    func delete() { token = nil }
}

// MARK: - 会话

@MainActor
final class PASessionStore: ObservableObject {
    static let shared = PASessionStore()

    enum State: Equatable {
        case loading
        case signedOut
        case signedIn(PAMe)
        case unverified       // 有令牌，但暂时无法验证
        case expired          // 服务器明确判定失效
        case offline(PAMe)    // 网络错误，保留上次已登录身份
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var lastError: String?
    @Published private(set) var isBusy = false

    private let client: PAAPIClient
    private let storage: PATokenStorage
    private let renewalClient: PASessionRenewalClient
    private let metadata: PASessionMetadataStore
    private let defaults = UserDefaults.standard
    private let meKey = "pa.session.me"
    let demoMode: Bool

    /// 服务端会话期限（由登录/续期响应给出）。缺失时不敢猜，也不会自动延长。
    @Published private(set) var expiresAt: Date?
    @Published private(set) var renewalState: PASessionRenewalState = .idle

    private var renewalTask: Task<Void, Never>?
    private var autoRenewalTask: Task<Void, Never>?
    private var renewalSerial = 0
    private var accountGeneration = 0
    private var lastRenewalAt: Date?
    private var isForeground = true

    init(client: PAAPIClient = PAAPIClient(),
         storage: PATokenStorage = KeychainTokenStorage(),
         demoMode: Bool? = nil,
         renewal: PASessionRenewalClient = RemoteSessionRenewalClient(),
         metadata: PASessionMetadataStore = UserDefaultsSessionMetadata()) {
        self.client = client
        self.storage = storage
        self.renewalClient = renewal
        self.metadata = metadata
        if let demoMode {
            self.demoMode = demoMode
        } else {
            #if DEBUG
            self.demoMode = ProcessInfo.processInfo.environment["PA_DEMO"] == "1"
            #else
            self.demoMode = false
            #endif
        }
        expiresAt = metadata.readExpiry()
    }

    static let demoMe = PAMe(userId: "demo-user", email: "demo@local", displayName: "演示账号")

    var token: String? { storage.read() }

    var cachedMe: PAMe? {
        guard let data = defaults.data(forKey: meKey) else { return nil }
        return try? PAJSON.decoder.decode(PAMe.self, from: data)
    }

    var accountId: String? {
        switch state {
        case .signedIn(let me), .offline(let me): return me.userId
        default: return cachedMe?.userId
        }
    }

    var isSignedIn: Bool { demoMode || token != nil }

    var displayName: String? {
        switch state {
        case .signedIn(let me), .offline(let me): return me.displayName
        default: return cachedMe?.displayName
        }
    }

    var email: String? {
        switch state {
        case .signedIn(let me), .offline(let me): return me.email
        default: return cachedMe?.email
        }
    }

    /// 启动时按接口验证缓存的令牌；网络错误不清除会话。
    func bootstrap() async {
        if demoMode {
            state = .signedIn(Self.demoMe)
            return
        }
        guard token != nil else {
            state = .signedOut
            return
        }
        state = .loading
        do {
            let me = try await client.me()
            cache(me)
            state = .signedIn(me)
        } catch let error as PAError {
            if let reason = clearReason(for: error) {
                clearLocal()
                state = .expired
                lastError = reason
            } else {
                switch error.kind {
                case .offline, .timeout, .server:
                    if let me = cachedMe {
                        state = .offline(me)
                    } else {
                        state = .unverified
                    }
                default:
                    // 未知 401/普通业务 403：保留本机会话，不全局登出。
                    state = .unverified
                }
                lastError = error.userMessage
            }
        } catch {
            state = .unverified
            lastError = "无法验证会话。"
        }
    }

    // MARK: - 滚动续期

    /// 冷启动：先按 /auth/me 恢复身份，再走一次续期（后端未实现时如实标记，不延长期限）。
    func restoreAndRenewIfPossible() async {
        await bootstrap()
        switch state {
        case .signedIn, .offline:
            await renew(force: true)
        default:
            break
        }
    }

    /// 回前台统一入口：去抖后续期；失败不清理凭据，也不把离线算作续期成功。
    func handleForeground() async {
        isForeground = true
        guard !demoMode, token != nil else { return }
        // 回前台即续期：同一会话由服务端向后延长，45 秒去抖 + single-flight 防抖。
        await renew(force: true)
        scheduleAutoRenewal()
    }

    /// 进入后台只停掉本地定时检查；不申请后台保活，也不取消服务端任务。
    func handleBackground() {
        isForeground = false
        autoRenewalTask?.cancel()
        autoRenewalTask = nil
    }

    /// 续期的唯一入口。并发调用通过 single-flight 合并；45 秒内不重复请求。
    /// 只有服务端明确判定过期/撤销/无效才会清凭据。
    func renew(force: Bool = false) async {
        guard !demoMode else { return }
        guard token != nil else {
            expiresAt = nil
            metadata.saveExpiry(nil)
            renewalState = .idle
            return
        }
        if let inFlight = renewalTask {
            await inFlight.value
            return
        }
        if !force {
            if let last = lastRenewalAt, Date().timeIntervalSince(last) < 45 { return }
            guard needsRenewalSoon else { return }
        }
        let serial = renewalSerial + 1
        renewalSerial = serial
        let generation = accountGeneration
        renewalState = .renewing
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.performRenewal(serial: serial, generation: generation)
        }
        renewalTask = task
        await task.value
        if renewalSerial == serial { renewalTask = nil }
    }

    /// 期限未知时视为需要校验；已知期限则只在到期前 20 分钟续期。
    private var needsRenewalSoon: Bool {
        guard let expiresAt else { return true }
        return expiresAt.timeIntervalSinceNow < 20 * 60
    }

    private func performRenewal(serial: Int, generation: Int) async {
        guard let currentToken = token else { return }
        lastRenewalAt = Date()
        do {
            let response = try await renewalClient.renew(using: currentToken)
            guard serial == renewalSerial, generation == accountGeneration else { return }
            applyRenewal(response)
        } catch let error as PAError {
            guard serial == renewalSerial, generation == accountGeneration else { return }
            handleRenewalFailure(error)
        } catch {
            guard serial == renewalSerial, generation == accountGeneration else { return }
            handleRenewalFailure(PAError(kind: .server, status: nil, code: "renew_transport",
                                         message: "续期请求失败。", retryable: true,
                                         requestId: nil, retryAfter: nil))
        }
    }

    private func applyRenewal(_ response: PARenewalResponse) {
        // 实际契约不返回新令牌；声称轮换却拿不到新令牌时，不能假装本地已更新。
        guard !response.tokenRotated else {
            let message = "服务器声明轮换了凭据，却没有下发新令牌；本机凭据保持不变。"
            renewalState = .failed(message)
            lastError = message
            return
        }
        cache(response.user)
        expiresAt = response.expiresAt
        metadata.saveExpiry(response.expiresAt)
        renewalState = .renewed(response.expiresAt)
        lastError = nil
        // 续期也带回账号信息：离线转回在线，账号以服务端响应为准。
        state = .signedIn(response.user)
        scheduleAutoRenewal()
    }

    /// 只有会话接口明确返回这些错误码才清理凭据；普通业务 403 不得全局登出。
    private func clearReason(for error: PAError) -> String? {
        switch error.code {
        case "invalid_session", "session_expired", "session_revoked":
            return "服务器判定会话已失效，请重新登录。"
        case "account_disabled":
            return "账号不可用。本机已退出该账号，请联系管理员。"
        default:
            return nil
        }
    }

    private func handleRenewalFailure(_ error: PAError) {
        if let reason = clearReason(for: error) {
            clearLocal()
            state = .expired
            renewalState = .failed(reason)
            lastError = reason
            return
        }
        switch error.kind {
        case .unauthenticated, .forbidden:
            // 401/403 但错误码不认识：可能是业务资源权限问题，保留凭据。
            let message = "服务器拒绝了本次续期（(error.code)），本机会话未清理。"
            renewalState = .failed(message)
            lastError = message
        case .notFound:
            // 契约未落地：保留凭据与原期限，明确告诉用户目前无法续期。
            let message = "后端尚未提供续期接口；会话保持原期限，不会自动延长。"
            renewalState = .unsupported(message)
            lastError = message
        case .offline, .timeout, .server, .rateLimited, .uncertain:
            let message = "暂时无法验证会话，未延长有效期。"
            renewalState = .failed(message)
            lastError = message
            if let me = cachedMe, case .signedIn = state {
                state = .offline(me)
            } else if state != .expired {
                state = .unverified
            }
        default:
            renewalState = .failed(error.userMessage)
            lastError = error.userMessage
        }
    }

    /// 前台长时间停留时，在到期前 10 分钟续期；进入后台即取消，不做后台保活。
    private func scheduleAutoRenewal() {
        autoRenewalTask?.cancel()
        autoRenewalTask = nil
        guard isForeground, !demoMode, token != nil, let expiresAt else { return }
        let delay = expiresAt.timeIntervalSinceNow - 10 * 60
        guard delay > 1 else { return }
        autoRenewalTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            await self.renew(force: true)
        }
    }

    @discardableResult
    func login(email: String, password: String) async -> Bool {
        guard !isBusy else { return false }
        isBusy = true
        defer { isBusy = false }
        lastError = nil
        do {
            let response = try await client.login(email: email, password: password)
            guard storage.save(response.token) else {
                lastError = "无法安全保存登录状态。"
                return false
            }
            let me = try await client.me()
            cache(me)
            accountGeneration += 1
            renewalSerial += 1
            renewalState = .idle
            lastRenewalAt = nil
            expiresAt = response.expiresAt
            metadata.saveExpiry(response.expiresAt)
            state = .signedIn(me)
            scheduleAutoRenewal()
            return true
        } catch let error as PAError {
            lastError = error.kind == .unauthenticated ? "邮箱或密码不正确。" : error.userMessage
            return false
        } catch {
            lastError = "登录失败，请稍后再试。"
            return false
        }
    }

    struct LogoutOutcome {
        let remoteRevoked: Bool
    }

    /// 先通知服务器撤销会话，再清理本地。
    /// 本机退出与服务器撤销是两件事：远端失败时保留明确提示，不当作已解绑。
    @discardableResult
    func logout() async -> LogoutOutcome {
        isBusy = true
        defer { isBusy = false }
        // 先清本机会话：即使撤销请求还没返回就被杀进程，重开也不会复活旧账号。
        let tokenToRevoke = token
        clearLocal()
        state = .signedOut
        var remoteRevoked = true
        if let tokenToRevoke {
            do {
                try await client.logout(using: tokenToRevoke)
            } catch {
                remoteRevoked = false
            }
        }
        renewalState = .idle
        lastError = remoteRevoked
            ? nil
            : "本机已退出，但未能确认服务器已撤销会话；该设备的推送解绑也可能未完成。"
        return LogoutOutcome(remoteRevoked: remoteRevoked)
    }

    func expireSession() {
        clearLocal()
        state = .expired
    }

    func clearError() { lastError = nil }

    private func cache(_ me: PAMe) {
        if let data = try? PAJSON.encoder.encode(me) {
            defaults.set(data, forKey: meKey)
        }
    }

    /// 凭据唯一清理点：令牌、身份缓存、期限和在途续期结果一起作废。
    private func clearLocal() {
        storage.delete()
        defaults.removeObject(forKey: meKey)
        expiresAt = nil
        metadata.saveExpiry(nil)
        autoRenewalTask?.cancel()
        autoRenewalTask = nil
        accountGeneration += 1
        renewalSerial += 1
        lastRenewalAt = nil
    }
}
