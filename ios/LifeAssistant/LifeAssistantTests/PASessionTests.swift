import XCTest
@testable import LifeAssistant

final class StubURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, [String: String], Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (status, headers, data) = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                           httpVersion: nil, headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

@MainActor
final class PASessionTests: XCTestCase {
    private var session: URLSession!
    private var storage: MemoryTokenStorage!

    override func setUp() {
        super.setUp()
        storage = MemoryTokenStorage()
        UserDefaults.standard.removeObject(forKey: "pa.session.me")
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        session = URLSession(configuration: config)
        StubURLProtocol.handler = nil
    }

    private func makeStore() -> PASessionStore {
        let storage = storage!
        let client = PAAPIClient(session: session, tokenProvider: { storage.read() })
        return PASessionStore(client: client, storage: storage, demoMode: false)
    }

    private func meJSON() -> Data {
        Data("""
        {"user_id":"u1","email":"me@example.com","display_name":"本地用户"}
        """.utf8)
    }

    private func loginJSON() -> Data {
        Data("""
        {"token":"session-token","expires_at":"2030-01-01T00:00:00Z","user_id":"u1"}
        """.utf8)
    }

    func testLoginSuccessStoresTokenAndIdentity() async {
        StubURLProtocol.handler = { request in
            switch request.url?.path {
            case "/api/v1/auth/login": return (200, [:], self.loginJSON())
            case "/api/v1/auth/me": return (200, [:], self.meJSON())
            default: return (404, [:], Data("{}".utf8))
            }
        }
        let store = makeStore()

        let ok = await store.login(email: "me@example.com", password: "pw")

        XCTAssertTrue(ok)
        XCTAssertEqual(storage.read(), "session-token")
        XCTAssertEqual(store.state, .signedIn(PAMe(userId: "u1", email: "me@example.com", displayName: "本地用户")))
        XCTAssertEqual(store.accountId, "u1")
    }


    func testLoginFailureDoesNotCreateSession() async {
        StubURLProtocol.handler = { _ in
            (401, [:], Data("""
            {"error":{"code":"unauthenticated","message":"邮箱或密码不正确。","retryable":false},"request_id":"r1"}
            """.utf8))
        }
        let store = makeStore()

        let ok = await store.login(email: "bad@example.com", password: "wrong")

        XCTAssertFalse(ok)
        XCTAssertNil(storage.read())
        XCTAssertEqual(store.lastError, "邮箱或密码不正确。")
        XCTAssertEqual(store.state, .loading)
    }

    func testBootstrap401ExpiresAndClearsToken() async {
        storage?.save("stale-token")
        StubURLProtocol.handler = { _ in
            (401, [:], Data("""
            {"error":{"code":"invalid_session","message":"expired","retryable":false},"request_id":null}
            """.utf8))
        }
        let store = makeStore()

        await store.bootstrap()

        XCTAssertEqual(store.state, .expired)
        XCTAssertNil(storage.read())
    }

    func testBootstrapNetworkFailureKeepsCachedIdentity() async {
        StubURLProtocol.handler = { request in
            switch request.url?.path {
            case "/api/v1/auth/login": return (200, [:], self.loginJSON())
            case "/api/v1/auth/me": return (200, [:], self.meJSON())
            default: return (404, [:], Data())
            }
        }
        let store = makeStore()
        _ = await store.login(email: "me@example.com", password: "pw")

        StubURLProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
        await store.bootstrap()

        guard case .offline(let me) = store.state else {
            return XCTFail("网络失败不应直接登出")
        }
        XCTAssertEqual(me.userId, "u1")
        XCTAssertEqual(storage.read(), "session-token")
    }

    func testLogoutOfflineDistinguishesLocalExitFromRemoteRevocation() async {
        StubURLProtocol.handler = { request in
            switch request.url?.path {
            case "/api/v1/auth/login": return (200, [:], self.loginJSON())
            case "/api/v1/auth/me": return (200, [:], self.meJSON())
            default: throw URLError(.notConnectedToInternet)
            }
        }
        let store = makeStore()
        _ = await store.login(email: "me@example.com", password: "pw")

        let outcome = await store.logout()

        XCTAssertFalse(outcome.remoteRevoked)
        XCTAssertEqual(store.state, .signedOut)
        XCTAssertNil(storage.read())
        XCTAssertTrue(store.lastError?.contains("未能确认") == true)
    }

    func testLogoutRevokesAndClearsLocalSession() async {
        StubURLProtocol.handler = { request in
            switch request.url?.path {
            case "/api/v1/auth/login": return (200, [:], self.loginJSON())
            case "/api/v1/auth/me": return (200, [:], self.meJSON())
            case "/api/v1/auth/logout": return (204, [:], Data())
            default: return (404, [:], Data())
            }
        }
        let store = makeStore()
        _ = await store.login(email: "me@example.com", password: "pw")

        await store.logout()

        XCTAssertEqual(store.state, .signedOut)
        XCTAssertNil(storage.read())
        XCTAssertNil(store.cachedMe)
    }
}


final class PAKeychainCacheTests: XCTestCase {
    func testDeleteInvalidatesSuccessfulReadCache() {
        let previous = PAKeychain.readToken()
        defer {
            if let previous { _ = PAKeychain.saveToken(previous) } else { PAKeychain.deleteToken() }
        }
        let token = "cache-test-" + UUID().uuidString
        XCTAssertTrue(PAKeychain.saveToken(token))
        XCTAssertEqual(PAKeychain.readToken(), token)
        PAKeychain.deleteToken()
        XCTAssertNil(PAKeychain.readToken(), "显式清除必须同时清掉进程内缓存")
        XCTAssertNil(PAKeychain.readTokenDirect())
    }
}
