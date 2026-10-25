import Foundation

enum PAConfig {
    static var baseURL: URL {
        #if DEBUG
        if let raw = ProcessInfo.processInfo.environment["PA_API_BASE_URL"], let url = URL(string: raw) {
            return url
        }
        #endif
        if let raw = Bundle.main.object(forInfoDictionaryKey: "PAApiBaseURL") as? String,
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://127.0.0.1:8010")!
    }
}

enum PAJSON {
    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            let withFraction = ISO8601DateFormatter()
            withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = withFraction.date(from: raw) { return date }
            let plain = ISO8601DateFormatter()
            plain.formatOptions = [.withInternetDateTime]
            if let date = plain.date(from: raw) { return date }
            // PA 使用 SQLite，时间戳可能不带时区；服务端存的是 UTC，按 UTC 解释。
            let naive = DateFormatter()
            naive.locale = Locale(identifier: "en_US_POSIX")
            naive.timeZone = TimeZone(secondsFromGMT: 0)
            for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss"] {
                naive.dateFormat = format
                if let date = naive.date(from: raw) { return date }
            }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "时间格式无效")
        }
        return decoder
    }()

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()
}

struct PAAPIClient {
    var baseURL: URL
    var session: URLSession
    var tokenProvider: () -> String?

    init(baseURL: URL = PAConfig.baseURL,
         session: URLSession? = nil,
         tokenProvider: @escaping () -> String? = { PAKeychain.readToken() }) {
        self.baseURL = baseURL
        self.tokenProvider = tokenProvider
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 45
            config.timeoutIntervalForResource = 90
            config.connectionProxyDictionary = [:]
            self.session = URLSession(configuration: config)
        }
    }

    // MARK: Auth

    func login(email: String, password: String) async throws -> PALoginResponse {
        struct Body: Encodable { let email: String; let password: String }
        return try await send("/api/v1/auth/login", method: "POST",
                              body: Body(email: email, password: password),
                              expect: PALoginResponse.self)
    }

    /// 退出时用调用方给出的令牌撤销该次会话；本机凭据会先被清理，
    /// 因此不能依赖 tokenProvider 再读 Keychain。
    func logout(using token: String) async throws {
        _ = try await sendRaw("/api/v1/auth/logout", method: "POST", tokenOverride: token)
    }

    func me() async throws -> PAMe {
        try await send("/api/v1/auth/me", expect: PAMe.self)
    }

    /// 实际契约：`POST /api/v1/auth/session/renew`，不发送 body。
    /// 404 只作为兼容状态处理；任何失败都不在这里伪造成功或延长本地期限。
    func renewSession() async throws -> PARenewalResponse {
        try await send("/api/v1/auth/session/renew", method: "POST",
                       expect: PARenewalResponse.self)
    }

    // MARK: Asks

    func listAsks(limit: Int = 20) async throws -> [AskRead] {
        try await send("/api/v1/asks?limit=\(limit)", expect: [AskRead].self)
    }

    func createAsk(text: String, idempotencyKey: String) async throws -> AskCreateResponse {
        struct Body: Encodable {
            let text: String
            let idempotencyKey: String
            enum CodingKeys: String, CodingKey {
                case text
                case idempotencyKey = "idempotency_key"
            }
        }
        return try await send("/api/v1/asks", method: "POST",
                              body: Body(text: text, idempotencyKey: idempotencyKey),
                              expect: AskCreateResponse.self)
    }

    func getAsk(id: String) async throws -> AskRead {
        try await send("/api/v1/asks/\(id)", expect: AskRead.self)
    }

    func cancelAsk(id: String) async throws -> AskRead {
        try await send("/api/v1/asks/\(id)/cancel", method: "POST", expect: AskRead.self)
    }

    // MARK: Core

    private func send<T: Decodable>(_ path: String, method: String = "GET",
                                    body: Encodable? = nil,
                                    expect: T.Type) async throws -> T {
        let data = try await sendRaw(path, method: method, body: body)
        do {
            return try PAJSON.decoder.decode(T.self, from: data)
        } catch {
            throw PAError(kind: .server, status: nil, code: "decode_failed",
                          message: "数据格式与约定不一致。", retryable: false,
                          requestId: nil, retryAfter: nil)
        }
    }

    @discardableResult
    private func sendRaw(_ path: String, method: String = "GET", body: Encodable? = nil,
                         tokenOverride: String? = nil) async throws -> Data {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw PAError(kind: .server, status: nil, code: "bad_url",
                          message: "接口地址无效。", retryable: false, requestId: nil, retryAfter: nil)
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = try PAJSON.encoder.encode(AnyEncodable(body))
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let effectiveToken = tokenOverride ?? tokenProvider()
        if let token = effectiveToken, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            if Task.isCancelled {
                throw PAError(kind: .cancelled, status: nil, code: "cancelled",
                              message: "已取消。", retryable: false, requestId: nil, retryAfter: nil)
            }
            throw transportError(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw PAError(kind: .server, status: nil, code: "bad_response",
                          message: "服务器响应无效。", retryable: true, requestId: nil, retryAfter: nil)
        }
        if (200..<300).contains(http.statusCode) { return data }
        throw decodeError(http: http, data: data)
    }

    private func transportError(_ error: Error) -> PAError {
        let code = (error as? URLError)?.code
        switch code {
        case .timedOut:
            return PAError(kind: .timeout, status: nil, code: "timeout",
                           message: "请求超时。", retryable: true, requestId: nil, retryAfter: nil)
        case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost, .cannotFindHost:
            return PAError(kind: .offline, status: nil, code: "offline",
                           message: "网络不可用。", retryable: true, requestId: nil, retryAfter: nil)
        default:
            return PAError(kind: .server, status: nil, code: "transport",
                           message: "请求失败。", retryable: true, requestId: nil, retryAfter: nil)
        }
    }

    private func decodeError(http: HTTPURLResponse, data: Data) -> PAError {
        let envelope = try? PAJSON.decoder.decode(PAErrorEnvelope.self, from: data)
        let code = envelope?.error.code ?? "http_\(http.statusCode)"
        let message = envelope?.error.message ?? "请求失败（\(http.statusCode)）。"
        let retryable = envelope?.error.retryable ?? (http.statusCode >= 500 || http.statusCode == 429)
        let retryAfter = http.value(forHTTPHeaderField: "Retry-After").flatMap(TimeInterval.init)

        let kind: PAError.Kind
        switch http.statusCode {
        case 401: kind = .unauthenticated
        case 403: kind = .forbidden
        case 404: kind = .notFound
        case 409: kind = .conflict
        case 422: kind = .validation
        case 429: kind = .rateLimited
        case 408, 504: kind = .timeout
        case 500...599: kind = .server
        default: kind = .validation
        }
        return PAError(kind: kind, status: http.statusCode, code: code,
                       message: message, retryable: retryable,
                       requestId: envelope?.requestId, retryAfter: retryAfter)
    }
}

private struct AnyEncodable: Encodable {
    private let closure: (Encoder) throws -> Void
    init(_ value: Encodable) { closure = value.encode }
    func encode(to encoder: Encoder) throws { try closure(encoder) }
}
