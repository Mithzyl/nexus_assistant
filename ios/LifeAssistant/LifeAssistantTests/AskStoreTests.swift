import XCTest
@testable import LifeAssistant

final class StubAskRepository: AskRepository {
    var listResult: Result<[AskRead], Error> = .success([])
    var createHandler: ((String, String) async throws -> AskCreateResponse)?
    var getHandler: ((String) async throws -> AskRead)?
    var cancelHandler: ((String) async throws -> AskRead)?
    var createdKeys: [String] = []
    var createdTexts: [String] = []
    var verifyHandler: (([String]) async throws -> [AskVerifyItem])?
    var verifyRequests: [[String]] = []

    func listAsks(limit: Int) async throws -> [AskRead] {
        try listResult.get()
    }

    func createAsk(text: String, idempotencyKey: String) async throws -> AskCreateResponse {
        createdKeys.append(idempotencyKey)
        createdTexts.append(text)
        guard let createHandler else {
            throw PAError(kind: .server, status: nil, code: "no_handler", message: "no handler",
                          retryable: false, requestId: nil, retryAfter: nil)
        }
        return try await createHandler(text, idempotencyKey)
    }

    func getAsk(id: String) async throws -> AskRead {
        guard let getHandler else {
            throw PAError(kind: .server, status: nil, code: "no_handler", message: "no handler",
                          retryable: false, requestId: nil, retryAfter: nil)
        }
        return try await getHandler(id)
    }

    func cancelAsk(id: String) async throws -> AskRead {
        guard let cancelHandler else {
            throw PAError(kind: .server, status: nil, code: "no_handler", message: "no handler",
                          retryable: false, requestId: nil, retryAfter: nil)
        }
        return try await cancelHandler(id)
    }

    func verifyAsks(localRunIDs: [String]) async throws -> [AskVerifyItem] {
        verifyRequests.append(localRunIDs)
        if let verifyHandler { return try await verifyHandler(localRunIDs) }
        return localRunIDs.map { AskVerifyItem(localRunId: $0, classification: .chat) }
    }
}

@MainActor
final class AskStoreTests: XCTestCase {
    private var account = "user-1"

    private func makeStore(_ stub: StubAskRepository,
                           demoMode: Bool = true) -> (AskStore, AskLocalStore) {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("ask-tests-" + UUID().uuidString)
        let local = AskLocalStore(root: directory)
        let store = AskStore(repository: stub, localStore: local, demoMode: demoMode,
                             accountProvider: { [weak self] in self?.account })
        return (store, local)
    }

    private func read(_ id: String, _ status: AskStatus, answer: String? = nil,
                      error: String? = nil, completed: Date? = nil) -> AskRead {
        AskRead(localRunId: id, nexusRunId: nil, workflowExecutionId: nil, status: status,
                replayed: false, requestText: "q", lastEventSequence: 0, errorCode: error,
                answer: answer, completedAt: completed, createdAt: Date().addingTimeInterval(-30))
    }

    func testSendGets202ThenRefreshYieldsAnswer() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-1", nexusRunId: nil,
                              workflowExecutionId: nil, status: .pending, replayed: false)
        }
        let (store, _) = makeStore(stub)

        let ok = await store.send("咖啡机退款到哪一步了？")
        XCTAssertTrue(ok)
        XCTAssertEqual(store.items.first?.serverId, "ask-1")
        XCTAssertEqual(store.items.first?.phase, .queued)

        stub.getHandler = { _ in self.read("ask-1", .succeeded, answer: "已到账。", completed: Date()) }
        await store.refresh(store.items[0].id)

        XCTAssertEqual(store.items.first?.phase, .succeeded)
        XCTAssertEqual(store.items.first?.answer, "已到账。")
        store.pausePolling()
    }

    func testDoubleTapCreatesOnlyOneRequest() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            try await Task.sleep(nanoseconds: 120_000_000)
            return AskCreateResponse(localRunId: "ask-1", nexusRunId: nil,
                                     workflowExecutionId: nil, status: .pending, replayed: false)
        }
        let (store, _) = makeStore(stub)

        async let first = store.send("同一句话")
        async let second = store.send("同一句话")
        _ = await (first, second)

        XCTAssertEqual(stub.createdKeys.count, 1)
        store.pausePolling()
    }

    func testCreateTimeoutKeepsKeyAndRetryReusesIt() async {
        let stub = StubAskRepository()
        let timeout = PAError(kind: .timeout, status: nil, code: "timeout", message: "timeout",
                              retryable: true, requestId: nil, retryAfter: nil)
        var attempts = 0
        stub.createHandler = { _, key in
            attempts += 1
            if attempts == 1 { throw timeout }
            return AskCreateResponse(localRunId: "ask-recovered", nexusRunId: nil,
                                     workflowExecutionId: nil, status: .pending, replayed: true)
        }
        let (store, _) = makeStore(stub)

        _ = await store.send("超时的问题")
        XCTAssertEqual(store.items.first?.phase, .submissionUncertain)
        let key = store.items[0].idempotencyKey

        await store.retrySubmission(store.items[0].id)

        XCTAssertEqual(stub.createdKeys, [key, key])
        XCTAssertEqual(store.items.first?.serverId, "ask-recovered")
        store.pausePolling()
    }

    func testUnknownReconcilesToSuccessWithoutNewRun() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-unknown", nexusRunId: nil,
                              workflowExecutionId: nil, status: .pending, replayed: false)
        }
        stub.getHandler = { _ in self.read("ask-unknown", .unknown, error: "nexus_event_stream_incomplete") }
        let (store, _) = makeStore(stub)
        _ = await store.send("状态未知的问题")

        await store.refresh(store.items[0].id)
        XCTAssertEqual(store.items.first?.phase, .unknown)
        XCTAssertTrue(store.items.first?.errorMessage?.contains("暂不确定") == true)

        stub.getHandler = { _ in self.read("ask-unknown", .succeeded, answer: "补回来的回答", completed: Date()) }
        await store.refresh(store.items[0].id)

        XCTAssertEqual(store.items.first?.phase, .succeeded)
        XCTAssertEqual(stub.createdKeys.count, 1)
        store.pausePolling()
    }

    func testCancelConfirmedLocksAgainstLateSuccess() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-cancel", nexusRunId: nil,
                              workflowExecutionId: nil, status: .running, replayed: false)
        }
        stub.cancelHandler = { _ in self.read("ask-cancel", .cancelled, completed: Date()) }
        let (store, _) = makeStore(stub)
        _ = await store.send("要取消的问题")

        await store.cancel(store.items[0].id)
        XCTAssertEqual(store.items.first?.phase, .cancelled)

        // 迟到的成功响应不得改写已确认取消。
        stub.getHandler = { _ in self.read("ask-cancel", .succeeded, answer: "迟到的回答", completed: Date()) }
        await store.refresh(store.items[0].id)
        XCTAssertEqual(store.items.first?.phase, .cancelled)
        XCTAssertNil(store.items.first?.answer)
        store.pausePolling()
    }

    func testCancelNetworkFailureKeepsRunningAndAsksReconciliation() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-1", nexusRunId: nil,
                              workflowExecutionId: nil, status: .running, replayed: false)
        }
        stub.cancelHandler = { _ in
            throw PAError(kind: .offline, status: nil, code: "offline", message: "offline",
                          retryable: true, requestId: nil, retryAfter: nil)
        }
        let (store, _) = makeStore(stub)
        _ = await store.send("取消失败的问题")

        await store.cancel(store.items[0].id)

        XCTAssertEqual(store.items.first?.phase, .running)
        XCTAssertTrue(store.lastError?.contains("核对") == true)
        store.pausePolling()
    }

    func testSucceededWithEmptyAnswerIsNotInvented() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-empty", nexusRunId: nil,
                              workflowExecutionId: nil, status: .succeeded, replayed: false)
        }
        stub.getHandler = { _ in self.read("ask-empty", .succeeded, answer: nil, completed: Date()) }
        let (store, _) = makeStore(stub)
        _ = await store.send("空回答的问题")
        await store.refresh(store.items[0].id)

        XCTAssertEqual(store.items.first?.phase, .succeeded)
        XCTAssertNil(store.items.first?.answer)
        store.pausePolling()
    }

    func testUnauthorizedOnPollingMarksNeedsLogin() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-401", nexusRunId: nil,
                              workflowExecutionId: nil, status: .running, replayed: false)
        }
        stub.getHandler = { _ in
            throw PAError(kind: .unauthenticated, status: 401, code: "unauthenticated",
                          message: "expired", retryable: false, requestId: nil, retryAfter: nil)
        }
        let (store, _) = makeStore(stub)
        _ = await store.send("会过期的问题")
        await store.refresh(store.items[0].id)

        XCTAssertTrue(store.needsLogin)
        store.pausePolling()
    }

    func testRateLimitStopsHighFrequencyPolling() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-429", nexusRunId: nil,
                              workflowExecutionId: nil, status: .running, replayed: false)
        }
        stub.getHandler = { _ in
            throw PAError(kind: .rateLimited, status: 429, code: "rate_limited",
                          message: "slow down", retryable: true, requestId: nil, retryAfter: 12)
        }
        let (store, _) = makeStore(stub)
        _ = await store.send("限流的问题")
        await store.refresh(store.items[0].id)

        XCTAssertTrue(store.lastError?.contains("12") == true)
        store.pausePolling()
    }

    func testLocalRunIdsOutsideLatestHistoryAreStillRestored() async {
        let stub = StubAskRepository()
        let (store, local) = makeStore(stub)
        let now = Date()
        // 25 条历史，服务器列表只返回最新 20 条；最老的一条只能靠本地 local_run_id 恢复。
        var seeds: [AskItem] = []
        for index in 0..<25 {
            seeds.append(AskItem(
                id: "local-\(index)", question: "问题 \(index)", idempotencyKey: "key-\(index)",
                serverId: "old-\(index)", status: .running, phase: .running, answer: nil,
                completedAt: nil, createdAt: now.addingTimeInterval(TimeInterval(-index)),
                updatedAt: now, errorMessage: nil
            ))
        }
        local.save(seeds, account: "user-1")
        stub.listResult = .success(seeds.prefix(20).map { item in
            AskRead(localRunId: item.serverId!, nexusRunId: nil, workflowExecutionId: nil,
                    status: .running, replayed: false, requestText: item.question,
                    lastEventSequence: 0, errorCode: nil, answer: nil,
                    completedAt: nil, createdAt: item.createdAt)
        })
        stub.getHandler = { id in
            AskRead(localRunId: id, nexusRunId: nil, workflowExecutionId: nil,
                    status: .running, replayed: false, requestText: "q",
                    lastEventSequence: 0, errorCode: nil, answer: nil,
                    completedAt: nil, createdAt: now)
        }

        await store.bindAccount()

        XCTAssertEqual(store.items.count, 25)
        XCTAssertTrue(store.items.contains { $0.serverId == "old-24" },
                      "超出最新 20 条的旧请求仍应由本地 local_run_id 恢复")
        store.pausePolling()
    }

    func testItemsPersistPerAccountAndRestore() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-persist", nexusRunId: nil,
                              workflowExecutionId: nil, status: .pending, replayed: false)
        }
        let (store, local) = makeStore(stub)
        _ = await store.send("重启后要恢复的问题")
        store.pausePolling()

        let restored = AskStore(repository: stub, localStore: local, demoMode: true,
                                accountProvider: { [weak self] in self?.account })
        await restored.bindAccount()

        XCTAssertEqual(restored.items.first?.serverId, "ask-persist")
        XCTAssertEqual(restored.items.first?.question, "重启后要恢复的问题")
        restored.pausePolling()
    }

    func testTimelineReadsOldestFirstWhileItemsStayNewestFirst() async {
        let stub = StubAskRepository()
        let (store, local) = makeStore(stub)
        let now = Date()
        let older = AskItem(id: "local-old", question: "先问的", idempotencyKey: "key-old",
                            serverId: "srv-old", status: .succeeded, phase: .succeeded,
                            answer: "旧回答", completedAt: now.addingTimeInterval(-120),
                            createdAt: now.addingTimeInterval(-120), updatedAt: now, errorMessage: nil)
        let newer = AskItem(id: "local-new", question: "后问的", idempotencyKey: "key-new",
                            serverId: "srv-new", status: .succeeded, phase: .succeeded,
                            answer: "新回答", completedAt: now.addingTimeInterval(-10),
                            createdAt: now.addingTimeInterval(-10), updatedAt: now, errorMessage: nil)
        local.save([newer, older], account: "user-1")

        await store.bindAccount()

        XCTAssertEqual(store.items.map(\.id), ["local-new", "local-old"], "内部存储仍是最新在前")
        XCTAssertEqual(store.timeline.map(\.id), ["local-old", "local-new"],
                       "聊天时间线必须是旧的在上、新的在下")
        store.pausePolling()
    }

    func testNewQuestionIsAppendedToBottomOfTimeline() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-bottom", nexusRunId: nil,
                              workflowExecutionId: nil, status: .pending, replayed: false)
        }
        let (store, local) = makeStore(stub)
        let now = Date()
        local.save([AskItem(id: "local-old-2", question: "旧问题", idempotencyKey: "key-old-2",
                            serverId: "srv-old-2", status: .succeeded, phase: .succeeded,
                            answer: "旧回答", completedAt: now.addingTimeInterval(-300),
                            createdAt: now.addingTimeInterval(-300), updatedAt: now, errorMessage: nil)],
                   account: "user-1")

        await store.bindAccount()
        _ = await store.send("新问题")

        XCTAssertEqual(store.timeline.map(\.question), ["旧问题", "新问题"])
        XCTAssertEqual(store.timeline.last?.serverId, "ask-bottom")
        store.pausePolling()
    }

    func testLogoutClearsMemoryButKeepsAccountFile() async {
        let stub = StubAskRepository()
        stub.createHandler = { _, _ in
            AskCreateResponse(localRunId: "ask-x", nexusRunId: nil,
                              workflowExecutionId: nil, status: .pending, replayed: false)
        }
        let (store, local) = makeStore(stub)
        _ = await store.send("登出前的问题")
        store.clearForLogout()

        XCTAssertTrue(store.items.isEmpty)
        XCTAssertTrue(store.needsLogin)
        XCTAssertEqual(local.load(account: "user-1").count, 1)
        store.pausePolling()
    }

    // MARK: - 旧缓存核对

    private func legacyItem(_ id: String, serverId: String, phase: AskPhase = .succeeded) -> AskItem {
        AskItem(id: id, question: "q-\(id)", idempotencyKey: "key-\(id)", serverId: serverId,
                status: .succeeded, phase: phase, answer: "a", completedAt: Date(),
                createdAt: Date().addingTimeInterval(-60), updatedAt: Date(), errorMessage: nil)
    }

    func testVerifyClassifiesAndHidesNonChat() async {
        let stub = StubAskRepository()
        stub.verifyHandler = { ids in
            ids.map { id in
                switch id {
                case "run-chat": return AskVerifyItem(localRunId: id, classification: .chat)
                case "run-internal": return AskVerifyItem(localRunId: id, classification: .notChat)
                default: return AskVerifyItem(localRunId: id, classification: .unknown)
                }
            }
        }
        let (store, local) = makeStore(stub, demoMode: false)
        local.save([legacyItem("local-chat", serverId: "run-chat"),
                    legacyItem("local-internal", serverId: "run-internal"),
                    legacyItem("local-unknown", serverId: "run-unknown"),
                    AskItem(question: "还没提交的问题")],
                   account: "user-1")

        await store.bindAccount()
        await store.verifyCachedRuns(force: true)

        let byId = Dictionary(uniqueKeysWithValues: store.items.map { ($0.id, $0) })
        XCTAssertEqual(byId["local-chat"]?.chatClassification, .chat)
        XCTAssertEqual(byId["local-internal"]?.chatClassification, .notChat)
        XCTAssertEqual(byId["local-unknown"]?.chatClassification, .unknown)
        let timelineIDs = Set(store.timeline.map(\.id))
        XCTAssertFalse(timelineIDs.contains("local-internal"), "内部任务不能进入聊天列表")
        XCTAssertTrue(timelineIDs.contains("local-unknown"), "unknown 保留但会遮罩正文")
        XCTAssertTrue(timelineIDs.contains(byId.first { $0.value.serverId == nil }!.key),
                      "未提交用户输入必须保留")
        XCTAssertEqual(Set(stub.verifyRequests.first ?? []),
                       ["run-chat", "run-internal", "run-unknown"])
    }

    func testVerifyFailureKeepsLegacyItemsVisible() async {
        let stub = StubAskRepository()
        stub.verifyHandler = { _ in
            throw PAError(kind: .server, status: 503, code: "unavailable",
                          message: "暂时不可用", retryable: true, requestId: nil, retryAfter: nil)
        }
        let (store, local) = makeStore(stub, demoMode: false)
        local.save([legacyItem("local-chat", serverId: "run-chat")], account: "user-1")

        await store.bindAccount()
        await store.verifyCachedRuns(force: true)

        XCTAssertNil(store.items.first?.chatClassification, "核对失败不能当成 not_chat")
        XCTAssertEqual(store.timeline.count, 1, "核对失败时保留原有展示")
        XCTAssertTrue(store.lastError?.contains("核对") == true)
    }

    func testVerifyBatchesAtMostFifty() async {
        let stub = StubAskRepository()
        let (store, local) = makeStore(stub, demoMode: false)
        let items = (0..<51).map { legacyItem("local-\($0)", serverId: "run-\($0)") }
        local.save(items, account: "user-1")

        await store.bindAccount()
        await store.verifyCachedRuns(force: true)

        XCTAssertEqual(stub.verifyRequests.map(\.count), [50, 1])
        XCTAssertTrue(store.items.allSatisfy { $0.chatClassification == .chat })
    }


    func testVerificationMasksDuplicateCopiesAndDeduplicates() async {
        let stub = StubAskRepository()
        stub.verifyHandler = { ids in ids.map { AskVerifyItem(localRunId: $0, classification: .unknown) } }
        let (store, local) = makeStore(stub, demoMode: false)
        local.save([legacyItem("copy-a", serverId: "run-1"),
                    legacyItem("copy-b", serverId: "run-1")], account: "user-1")

        await store.bindAccount()
        await store.verifyCachedRuns(force: true)

        XCTAssertEqual(store.items.count, 1, "同一服务器编号的旧副本应合并")
        XCTAssertEqual(store.items.first?.chatClassification, .unknown)
        XCTAssertEqual(store.timeline.count, 1)
    }


    func testDedupeKeepsVerifiedClassification() async {
        let stub = StubAskRepository()
        var older = legacyItem("older", serverId: "run-1")
        older.chatClassification = .unknown
        var newer = legacyItem("newer", serverId: "run-1")
        newer.chatClassification = nil
        newer.updatedAt = older.updatedAt.addingTimeInterval(10)
        let (store, local) = makeStore(stub, demoMode: false)
        local.save([older, newer], account: "user-1")

        await store.bindAccount()

        XCTAssertEqual(store.items.count, 1)
        XCTAssertEqual(store.items.first?.chatClassification, .unknown,
                       "合并副本时不能丢掉服务器已核对分类")
    }

    func testVerifySkipsUnsubmittedInputs() async {
        let stub = StubAskRepository()
        let (store, local) = makeStore(stub, demoMode: false)
        local.save([AskItem(question: "只在本机，还没提交")], account: "user-1")

        await store.bindAccount()
        await store.verifyCachedRuns(force: true)

        XCTAssertTrue(stub.verifyRequests.isEmpty)
        XCTAssertEqual(store.timeline.count, 1)
        XCTAssertNil(store.timeline.first?.chatClassification)
    }
}

final class PAAskContractTests: XCTestCase {
    func testDecodesAskReadWithNullsAndFractionalTime() throws {
        let json = """
        {
          "local_run_id": "run-1",
          "nexus_run_id": "nexus-1",
          "workflow_execution_id": null,
          "status": "unknown",
          "replayed": false,
          "request_text": "问题",
          "last_event_sequence": 2,
          "error_code": "nexus_event_stream_incomplete",
          "answer": null,
          "completed_at": null,
          "created_at": "2026-10-23T02:00:00.123456Z"
        }
        """.data(using: .utf8)!

        let read = try PAJSON.decoder.decode(AskRead.self, from: json)
        XCTAssertEqual(read.status, .unknown)
        XCTAssertTrue(read.status.isTerminal)
        XCTAssertNil(read.workflowExecutionId)
        XCTAssertNil(read.answer)
        XCTAssertNotNil(read.createdAt)
    }

    func testDecodesLegacyNaiveDatabaseTimestampAsUTC() throws {
        let json = """
        {"local_run_id":"run-2","nexus_run_id":null,"workflow_execution_id":null,
         "status":"succeeded","replayed":false,"request_text":"q","last_event_sequence":5,
         "error_code":null,"answer":"答","completed_at":"2026-10-23T13:48:40.926169",
         "created_at":"2026-10-23T13:48:28.894324"}
        """.data(using: .utf8)!
        let read = try PAJSON.decoder.decode(AskRead.self, from: json)
        XCTAssertEqual(read.status, .succeeded)
        XCTAssertEqual(read.answer, "答")
        XCTAssertNotNil(read.completedAt)
        XCTAssertEqual(read.completedAt!.timeIntervalSince1970,
                       Date(timeIntervalSince1970: 1_792_763_320.926169).timeIntervalSince1970,
                       accuracy: 1.5)
    }

    func testDecodesOffsetTimestamp() throws {
        let json = """
        {"local_run_id":"run-3","nexus_run_id":null,"workflow_execution_id":null,
         "status":"succeeded","replayed":false,"request_text":"q","last_event_sequence":5,
         "error_code":null,"answer":"答","completed_at":"2026-10-23T21:48:40.926169+08:00",
         "created_at":"2026-10-23T21:48:28.894324+08:00"}
        """.data(using: .utf8)!
        let read = try PAJSON.decoder.decode(AskRead.self, from: json)
        XCTAssertEqual(read.completedAt!.timeIntervalSince1970,
                       Date(timeIntervalSince1970: 1_792_763_320.926169).timeIntervalSince1970,
                       accuracy: 0.01)
    }

    func testRejectsInvalidTimestamp() throws {
        let json = """
        {"local_run_id":"run-4","nexus_run_id":null,"workflow_execution_id":null,
         "status":"succeeded","replayed":false,"request_text":"q","last_event_sequence":5,
         "error_code":null,"answer":"答","completed_at":"not-a-time",
         "created_at":"2026-10-23T02:00:00Z"}
        """.data(using: .utf8)!
        XCTAssertThrowsError(try PAJSON.decoder.decode(AskRead.self, from: json))
    }

    func testDecodesErrorEnvelope() throws {
        let json = """
        {"error":{"code":"unauthenticated","message":"登录状态无效。","retryable":false},"request_id":"req-1"}
        """.data(using: .utf8)!
        let envelope = try PAJSON.decoder.decode(PAErrorEnvelope.self, from: json)
        XCTAssertEqual(envelope.error.code, "unauthenticated")
        XCTAssertEqual(envelope.requestId, "req-1")
    }

}
