import Foundation

// MARK: - 错误模型

struct CommuteAPIError: Error, LocalizedError, Equatable {
    enum Kind: Equatable {
        case unauthenticated
        case notFound
        case conflict
        case validation
        case rateLimited
        case timeout
        case offline
        case server
        case cancelled
    }

    let kind: Kind
    let status: Int?
    let code: String
    let message: String
    let retryable: Bool
    let requestId: String?

    var errorDescription: String? { message }

    var userMessage: String {
        switch kind {
        case .unauthenticated: return "登录状态已失效，请重新登录。"
        case .conflict: return "设置已在其他设备修改，请重新加载后再保存。"
        case .rateLimited: return "刷新太频繁，请稍后再试。"
        case .timeout: return "请求超时，稍后可以重试。"
        case .offline: return "当前网络不可用，显示的是本地缓存。"
        case .server: return "服务器暂时不可用，稍后可以重试。"
        case .cancelled: return "已取消。"
        default: return message
        }
    }

    static let missingAdvisory = CommuteAPIError(
        kind: .notFound, status: 404, code: "NO_ADVISORY",
        message: "还没有生成过建议。", retryable: false, requestId: nil
    )

    static let missingWeather = CommuteAPIError(
        kind: .notFound, status: 404, code: "NO_WEATHER",
        message: "还没有可用的天气记录。", retryable: false, requestId: nil
    )

    static func transport(_ error: Error) -> CommuteAPIError {
        if error is CancellationError { return cancelled }
        let urlError = error as? URLError
        switch urlError?.code {
        case .timedOut:
            return CommuteAPIError(kind: .timeout, status: nil, code: "timeout",
                                   message: "请求超时。", retryable: true, requestId: nil)
        case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost, .cannotFindHost:
            return CommuteAPIError(kind: .offline, status: nil, code: "offline",
                                   message: "网络不可用。", retryable: true, requestId: nil)
        default:
            return CommuteAPIError(kind: .server, status: nil, code: "transport",
                                   message: "请求失败。", retryable: true, requestId: nil)
        }
    }

    static let cancelled = CommuteAPIError(
        kind: .cancelled, status: nil, code: "cancelled",
        message: "已取消。", retryable: false, requestId: nil
    )
}

struct BarkStatusResponse: Decodable {
    let configured: Bool
    let autoRemindersEnabled: Bool
    let available: Bool
    let state: String
    let lastTestStatus: String?
    let lastTestAt: Date?

    enum CodingKeys: String, CodingKey {
        case configured, available, state
        case autoRemindersEnabled = "auto_reminders_enabled"
        case lastTestStatus = "last_test_status"
        case lastTestAt = "last_test_at"
    }
}

struct BarkTestResponse: Decodable {
    let requestSubmitted: Bool
    let status: String
    let accepted: Bool
    let message: String
    let testedAt: Date?

    enum CodingKeys: String, CodingKey {
        case status, accepted, message
        case requestSubmitted = "request_submitted"
        case testedAt = "tested_at"
    }
}

// MARK: - 配置与令牌

enum CommuteConfig {
    /// There is one PA service. Commute must share its base URL (including any
    /// development/local override) instead of maintaining a second default.
    static var baseURL: URL { PAConfig.baseURL }

    static var installationID: String {
        let key = "commute.installation.id"
        if let existing = UserDefaults.standard.string(forKey: key) {
            return existing
        }
        let value = UUID().uuidString.lowercased()
        UserDefaults.standard.set(value, forKey: key)
        return value
    }
}

/// 会话令牌最小存储；演示工程不引入账号体系，登录后仅保存 bearer token。
/// 通勤与问答共用同一个 PA 会话令牌，只存在 Keychain。
enum TokenStore {
    /// Read-only here: PASessionStore / PASession owns token writes and deletion.
    static var token: String? { PAKeychain.readToken() }
    static var isSignedIn: Bool { token?.isEmpty == false }
}

// MARK: - JSON 编解码

enum CommuteJSON {
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            if let date = parse(raw) { return date }
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "无法解析时间：\(raw)"
            )
        }
        return decoder
    }()

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()

    /// 服务端允许带小数秒的 UTC ISO 8601；两种格式都接受。
    static func parse(_ raw: String) -> Date? {
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: raw) { return date }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let date = plain.date(from: raw) { return date }
        // SQLite 可能丢失时区；服务端按 UTC 存储。
        let naive = DateFormatter()
        naive.locale = Locale(identifier: "en_US_POSIX")
        naive.timeZone = TimeZone(secondsFromGMT: 0)
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss"] {
            naive.dateFormat = format
            if let date = naive.date(from: raw) { return date }
        }
        return nil
    }
}

// MARK: - HTTP 客户端

struct CommuteAPIClient {
    var baseURL: URL = CommuteConfig.baseURL
    var session: URLSession
    var tokenProvider: () -> String?

    init(baseURL: URL = CommuteConfig.baseURL,
         session: URLSession? = nil,
         tokenProvider: @escaping () -> String? = { TokenStore.token }) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 15
            config.timeoutIntervalForResource = 30
            config.waitsForConnectivity = false
            // 本机可能配置了 HTTP_PROXY；联调时不希望环回请求被代理接管。
            config.connectionProxyDictionary = [:]
            self.session = URLSession(configuration: config)
        }
    }

    // MARK: Auth

    func login(email: String, password: String) async throws -> LoginResponse {
        struct Body: Encodable { let email: String; let password: String }
        return try await send("/api/v1/auth/login", method: "POST", body: Body(email: email, password: password), expect: LoginResponse.self)
    }

    func me() async throws -> Bool {
        struct Me: Decodable { let userId: String
            enum CodingKeys: String, CodingKey { case userId = "user_id" } }
        _ = try await send("/api/v1/auth/me", expect: Me.self)
        return true
    }

    // MARK: Plans

    func listPlans() async throws -> [CommutePlan] {
        try await send("/api/v1/commute-plans", expect: [CommutePlan].self)
    }

    func createPlan(_ draft: CommutePlanDraft) async throws -> CommutePlan {
        try await send("/api/v1/commute-plans", method: "POST", body: draft, expect: CommutePlan.self)
    }

    func updatePlan(id: String, draft: CommutePlanDraft, revision: Int) async throws -> CommutePlan {
        try await send(
            "/api/v1/commute-plans/\(id)", method: "PUT", body: draft,
            headers: ["If-Match": String(revision)], expect: CommutePlan.self
        )
    }

    func deletePlan(id: String) async throws {
        _ = try await sendRaw("/api/v1/commute-plans/\(id)", method: "DELETE")
    }

    // MARK: Checks & advisories

    /// 实际契约允许携带 `{plan_revision, route_snapshot}`；nil 表示不带快照的普通检查。
    func requestCheck(planId: String, idempotencyKey: String,
                      request: CheckCreateRequest?) async throws -> CommuteCheck {
        try await send(
            "/api/v1/commute-plans/\(planId)/checks", method: "POST",
            body: request,
            headers: ["Idempotency-Key": idempotencyKey], expect: CommuteCheck.self
        )
    }

    func check(id: String) async throws -> CommuteCheck {
        try await send("/api/v1/checks/\(id)", expect: CommuteCheck.self)
    }

    func latestAdvisory(planId: String) async throws -> CommuteAdvisory {
        try await send("/api/v1/commute-plans/\(planId)/advisories/latest", expect: CommuteAdvisory.self)
    }

    func latestWeather(planId: String, localServiceDate: String?) async throws -> PlanWeather {
        var path = "/api/v1/commute-plans/\(planId)/weather"
        if let date = localServiceDate, !date.isEmpty {
            path += "?local_service_date=\(date)"
        }
        return try await send(path, expect: PlanWeather.self)
    }

    func advisory(id: String) async throws -> CommuteAdvisory {
        try await send("/api/v1/advisories/\(id)", expect: CommuteAdvisory.self)
    }

    // MARK: Devices & actions

    func registerDevice(installationId: String, token: String, permission: String) async throws {
        struct Body: Encodable {
            let apnsToken: String
            let notificationPermission: String
            enum CodingKeys: String, CodingKey {
                case apnsToken = "apns_token"
                case notificationPermission = "notification_permission"
            }
        }
        _ = try await sendRaw(
            "/api/v1/push-devices/\(installationId)", method: "PUT",
            body: Body(apnsToken: token, notificationPermission: permission)
        )
    }

    func unregisterDevice(installationId: String) async throws {
        _ = try await sendRaw("/api/v1/push-devices/\(installationId)", method: "DELETE")
    }

    func reportAction(advisoryId: String, action: String, installationId: String, idempotencyKey: String) async throws {
        _ = try await sendRaw(
            "/api/v1/advisories/\(advisoryId)/actions", method: "POST",
            body: AdvisoryActionRequest(action: action, installationId: installationId),
            headers: ["Idempotency-Key": idempotencyKey]
        )
    }

    // MARK: Core

    private struct EmptyBody: Encodable {}

    private func makeRequest(_ path: String, method: String, body: Data?, headers: [String: String]) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw CommuteAPIError(kind: .server, status: nil, code: "bad_url",
                                  message: "接口地址无效。", retryable: false, requestId: nil)
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        if let token = tokenProvider() {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func send<T: Decodable>(
        _ path: String, method: String = "GET",
        body: Encodable? = nil, headers: [String: String] = [:], expect: T.Type
    ) async throws -> T {
        let data = try await sendRaw(path, method: method, body: body, headers: headers)
        if data.isEmpty {
            throw CommuteAPIError(kind: .server, status: nil, code: "empty_body",
                                  message: "服务器返回为空。", retryable: true, requestId: nil)
        }
        do {
            return try CommuteJSON.decoder.decode(T.self, from: data)
        } catch {
            throw CommuteAPIError(kind: .server, status: nil, code: "decode_failed",
                                  message: "数据格式与约定不一致。", retryable: false, requestId: nil)
        }
    }

    @discardableResult
    private func sendRaw(
        _ path: String, method: String = "GET",
        body: Encodable? = nil, headers: [String: String] = [:]
    ) async throws -> Data {
        let encoded: Data? = try body.map { try CommuteJSON.encoder.encode(AnyEncodable($0)) }
        let request = try makeRequest(path, method: method, body: encoded, headers: headers)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            if Task.isCancelled { throw CommuteAPIError.cancelled }
            throw CommuteAPIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw CommuteAPIError(kind: .server, status: nil, code: "bad_response",
                                  message: "服务器响应无效。", retryable: true, requestId: nil)
        }
        if (200..<300).contains(http.statusCode) {
            return data
        }
        throw decodeError(status: http.statusCode, data: data)
    }

    private func decodeError(status: Int, data: Data) -> CommuteAPIError {
        let envelope = try? CommuteJSON.decoder.decode(APIErrorEnvelope.self, from: data)
        let code = envelope?.error.code ?? "http_\(status)"
        let message = envelope?.error.message ?? "请求失败（\(status)）。"
        let retryable = envelope?.error.retryable ?? (status >= 500 || status == 429)

        let kind: CommuteAPIError.Kind
        switch status {
        case 401: kind = .unauthenticated
        case 403: kind = .unauthenticated
        case 404: kind = .notFound
        case 409: kind = .conflict
        case 422: kind = .validation
        case 429: kind = .rateLimited
        case 408, 504: kind = .timeout
        default: kind = status >= 500 ? .server : .validation
        }
        return CommuteAPIError(kind: kind, status: status, code: code, message: message,
                               retryable: retryable, requestId: envelope?.requestId)
    }
    // MARK: Bark

    func barkStatus() async throws -> BarkStatusResponse {
        try await send("/api/v1/commute/bark", expect: BarkStatusResponse.self)
    }

    func bindBark(deviceKey: String) async throws -> BarkStatusResponse {
        struct Body: Encodable { let device_key: String }
        return try await send("/api/v1/commute/bark/binding", method: "POST",
                              body: Body(device_key: deviceKey), expect: BarkStatusResponse.self)
    }

    func unbindBark() async throws -> BarkStatusResponse {
        try await send("/api/v1/commute/bark/binding", method: "DELETE", expect: BarkStatusResponse.self)
    }

    func setBarkAutoReminders(_ enabled: Bool) async throws -> BarkStatusResponse {
        struct Body: Encodable { let auto_reminders_enabled: Bool }
        return try await send("/api/v1/commute/bark", method: "PATCH",
                              body: Body(auto_reminders_enabled: enabled), expect: BarkStatusResponse.self)
    }

    func testBark() async throws -> BarkTestResponse {
        struct Body: Encodable { let confirm_send: Bool }
        return try await send("/api/v1/commute/bark/test", method: "POST",
                              body: Body(confirm_send: true), expect: BarkTestResponse.self)
    }


}

/// 允许以存在类型传入 Encodable 请求体。
private struct AnyEncodable: Encodable {
    private let encodeClosure: (Encoder) throws -> Void
    init(_ wrapped: Encodable) { encodeClosure = wrapped.encode }
    func encode(to encoder: Encoder) throws { try encodeClosure(encoder) }
}
