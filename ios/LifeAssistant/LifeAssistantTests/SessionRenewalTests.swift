import XCTest
@testable import LifeAssistant

final class CountingTokenStorage: PATokenStorage {
    var token: String?
    var saves = 0
    var failSaves = false

    init(token: String? = nil) { self.token = token }
    func read() -> String? { token }
    @discardableResult func save(_ token: String) -> Bool {
        saves += 1
        guard !failSaves else { return false }
        self.token = token
        return true
    }
    func delete() { token = nil }
}

final class MemorySessionMetadata: PASessionMetadataStore {
    var expiry: Date?
    init(_ expiry: Date? = nil) { self.expiry = expiry }
    func readExpiry() -> Date? { expiry }
    func saveExpiry(_ date: Date?) { expiry = date }
}

final class FakeRenewalClient: PASessionRenewalClient {
    var calls = 0
    var delayNanoseconds: UInt64 = 0
    var result: Result<PARenewalResponse, Error> = .success(PARenewalResponse(
        user: PAMe(userId: "u1", email: "me@example.com", displayName: "本地用户"),
        expiresAt: Date().addingTimeInterval(3600),
        serverTime: Date(),
        tokenRotated: false
    ))

    func renew(using token: String) async throws -> PARenewalResponse {
        calls += 1
        if delayNanoseconds > 0 { try? await Task.sleep(nanoseconds: delayNanoseconds) }
        return try result.get()
    }
}

@MainActor
final class PASessionRenewalTests: XCTestCase {
    private var storage: CountingTokenStorage!
    private var metadata: MemorySessionMetadata!
    private var renewal: FakeRenewalClient!
    private var urlSession: URLSession!

    private let meData = Data(#"{"user_id":"u1","email":"me@example.com","display_name":"本地用户"}"#.utf8)

    override func setUp() {
        super.setUp()
        storage = CountingTokenStorage(token: "session-token")
        metadata = MemorySessionMetadata()
        renewal = FakeRenewalClient()
        UserDefaults.standard.removeObject(forKey: "pa.session.me")
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        urlSession = URLSession(configuration: config)
        StubURLProtocol.handler = { [meData] request in
            switch request.url?.path {
            case "/api/v1/auth/me":
                return (200, [:], meData)
            case "/api/v1/auth/logout":
                return (204, [:], Data())
            default:
                return (404, [:], Data("{}".utf8))
            }
        }
    }

    private func makeStore() -> PASessionStore {
        let storage = storage!
        let client = PAAPIClient(session: urlSession, tokenProvider: { storage.read() })
        return PASessionStore(client: client, storage: storage, demoMode: false,
                              renewal: renewal, metadata: metadata)
    }

    func testLoginStoresExpiryAndLogoutClearsIt() async {
        StubURLProtocol.handler = { [meData] request in
            switch request.url?.path {
            case "/api/v1/auth/login":
                return (200, [:], Data(#"{"token":"session-token","expires_at":"2030-01-01T00:00:00Z","user_id":"u1"}"#.utf8))
            case "/api/v1/auth/me":
                return (200, [:], meData)
            case "/api/v1/auth/logout":
                return (204, [:], Data())
            default:
                return (404, [:], Data("{}".utf8))
            }
        }
        let store = makeStore()

        let ok = await store.login(email: "me@example.com", password: "pw")
        XCTAssertTrue(ok)
        XCTAssertEqual(store.expiresAt, ISO8601DateFormatter().date(from: "2030-01-01T00:00:00Z"))
        XCTAssertEqual(metadata.expiry, store.expiresAt)

        _ = await store.logout()
        XCTAssertNil(store.expiresAt)
        XCTAssertNil(metadata.expiry)
        XCTAssertNil(storage.token)
    }

    func testColdStartWithMissingEndpointKeepsTokenAndOriginalExpiry() async {
        let original = Date().addingTimeInterval(86_400)
        metadata.expiry = original
        renewal.result = .failure(PAError(kind: .notFound, status: 404, code: "not_found",
                                          message: "Not Found", retryable: false,
                                          requestId: "r1", retryAfter: nil))
        let store = makeStore()

        await store.restoreAndRenewIfPossible()

        XCTAssertEqual(storage.token, "session-token", "契约缺失不能清理凭据")
        XCTAssertEqual(store.expiresAt, original, "没有服务端响应就不能自行延长或重置期限")
        XCTAssertEqual(renewal.calls, 1)
        guard case .unsupported = store.renewalState else {
            return XCTFail("应标记为续期接口未实现")
        }
        guard case .signedIn = store.state else {
            return XCTFail("身份校验成功后应保持登录态")
        }
    }

    func testConcurrentRenewalsAreSingleFlight() async {
        renewal.delayNanoseconds = 150_000_000
        let store = makeStore()

        async let first: Void = store.renew(force: true)
        async let second: Void = store.renew(force: true)
        _ = await (first, second)

        XCTAssertEqual(renewal.calls, 1, "并发续期必须合并为一次请求")
    }

    func testRenewalAppliesServerExpiryAndIdentityWithoutTouchingToken() async {
        let newExpiry = Date().addingTimeInterval(5 * 86_400)
        let user = PAMe(userId: "u1", email: "me@example.com", displayName: "续期后的名字")
        renewal.result = .success(PARenewalResponse(user: user, expiresAt: newExpiry,
                                                    serverTime: Date(), tokenRotated: false))
        let store = makeStore()

        await store.renew(force: true)

        XCTAssertEqual(store.expiresAt, newExpiry)
        XCTAssertEqual(metadata.expiry, newExpiry)
        XCTAssertEqual(storage.saves, 0, "续期沿用原令牌，不应重写 Keychain")
        XCTAssertEqual(storage.token, "session-token")
        XCTAssertEqual(store.state, .signedIn(user), "账号以续期响应为准")
        guard case .renewed = store.renewalState else { return XCTFail("应记录续期成功") }
    }

    func testTokenRotatedClaimWithoutTokenIsNotFaked() async {
        let original = Date().addingTimeInterval(86_400)
        metadata.expiry = original
        renewal.result = .success(PARenewalResponse(
            user: PAMe(userId: "u1", email: "me@example.com", displayName: "本地用户"),
            expiresAt: Date().addingTimeInterval(6 * 86_400),
            serverTime: Date(),
            tokenRotated: true
        ))
        let store = makeStore()

        await store.renew(force: true)

        XCTAssertEqual(storage.token, "session-token")
        XCTAssertEqual(storage.saves, 0)
        XCTAssertEqual(store.expiresAt, original, "没有新令牌就不能采纳新期限")
        guard case .failed = store.renewalState else { return XCTFail("应报告契约不一致") }
    }

    func testAccountDisabledClearsSessionWithExplicitMessage() async {
        renewal.result = .failure(PAError(kind: .forbidden, status: 403, code: "account_disabled",
                                          message: "账号不可用。", retryable: false,
                                          requestId: "r3", retryAfter: nil))
        let store = makeStore()

        await store.renew(force: true)

        XCTAssertNil(storage.token)
        XCTAssertEqual(store.state, .expired)
        XCTAssertTrue(store.lastError?.contains("账号不可用") == true)
    }

    func testUnknownForbiddenDoesNotLogOutGlobally() async {
        renewal.result = .failure(PAError(kind: .forbidden, status: 403, code: "forbidden",
                                          message: "无权访问。", retryable: false,
                                          requestId: "r4", retryAfter: nil))
        let store = makeStore()

        await store.renew(force: true)

        XCTAssertEqual(storage.token, "session-token", "普通资源 403 不能全局登出")
        guard case .failed = store.renewalState else { return XCTFail("应保留凭据并提示") }
    }

    func testUnauthorizedRenewalClearsCredentials() async {
        renewal.result = .failure(PAError(kind: .unauthenticated, status: 401, code: "invalid_session",
                                          message: "invalid", retryable: false,
                                          requestId: "r2", retryAfter: nil))
        let store = makeStore()

        await store.renew(force: true)

        XCTAssertNil(storage.token)
        XCTAssertNil(metadata.expiry)
        XCTAssertEqual(store.state, .expired)
    }

    func testTransientRenewalFailureKeepsTokenAndExpiry() async {
        let original = Date().addingTimeInterval(86_400)
        metadata.expiry = original
        renewal.result = .failure(PAError(kind: .offline, status: nil, code: "offline",
                                          message: "offline", retryable: true,
                                          requestId: nil, retryAfter: nil))
        let store = makeStore()

        await store.renew(force: true)

        XCTAssertEqual(storage.token, "session-token")
        XCTAssertEqual(store.expiresAt, original)
        XCTAssertEqual(metadata.expiry, original)
        XCTAssertEqual(store.state, .unverified)
        guard case .failed = store.renewalState else { return XCTFail("离线不能算作续期成功") }
    }

    func testLogoutDropsLateRenewalResult() async {
        metadata.expiry = Date().addingTimeInterval(86_400)
        renewal.delayNanoseconds = 200_000_000
        renewal.result = .success(PARenewalResponse(
            user: PAMe(userId: "u1", email: "me@example.com", displayName: "本地用户"),
            expiresAt: Date().addingTimeInterval(9 * 86_400),
            serverTime: Date(),
            tokenRotated: false
        ))
        let store = makeStore()

        let task = Task { await store.renew(force: true) }
        try? await Task.sleep(nanoseconds: 40_000_000)
        _ = await store.logout()
        await task.value

        XCTAssertNil(storage.token)
        XCTAssertNil(metadata.expiry)
        XCTAssertEqual(store.state, .signedOut)
        XCTAssertNil(store.expiresAt)
        if case .renewed = store.renewalState { XCTFail("退出后不得写回迟到的续期结果") }
    }
}
