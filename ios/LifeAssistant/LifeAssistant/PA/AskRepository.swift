import Foundation

protocol AskRepository {
    func listAsks(limit: Int) async throws -> [AskRead]
    func createAsk(text: String, idempotencyKey: String) async throws -> AskCreateResponse
    func getAsk(id: String) async throws -> AskRead
    func cancelAsk(id: String) async throws -> AskRead
    func verifyAsks(localRunIDs: [String]) async throws -> [AskVerifyItem]
}

struct RemoteAskRepository: AskRepository {
    var client: PAAPIClient

    init(client: PAAPIClient = PAAPIClient()) { self.client = client }

    func listAsks(limit: Int) async throws -> [AskRead] { try await client.listAsks(limit: limit) }
    func createAsk(text: String, idempotencyKey: String) async throws -> AskCreateResponse {
        try await client.createAsk(text: text, idempotencyKey: idempotencyKey)
    }
    func getAsk(id: String) async throws -> AskRead { try await client.getAsk(id: id) }
    func cancelAsk(id: String) async throws -> AskRead { try await client.cancelAsk(id: id) }
    func verifyAsks(localRunIDs: [String]) async throws -> [AskVerifyItem] {
        try await client.verifyAsks(localRunIDs: localRunIDs).results
    }
}

enum AskRepositoryFactory {
    /// Mock 只在 DEBUG + 显式演示变量时启用；生产网络失败不会回退为假成功。
    static func make() -> AskRepository {
        #if DEBUG
        if ProcessInfo.processInfo.environment["PA_DEMO"] == "1" {
            return MockAskRepository.demo()
        }
        #endif
        return RemoteAskRepository()
    }

    static var isDemo: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.environment["PA_DEMO"] == "1"
        #else
        return false
        #endif
    }
}

/// 按账号隔离的本地记录；不保存密码，只保存问题、状态和回答缓存。
struct AskLocalStore {
    private let root: URL
    private let fileManager = FileManager.default

    init(root: URL? = nil) {
        if let root {
            self.root = root
        } else {
            let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? fileManager.temporaryDirectory
            self.root = base.appendingPathComponent("PA", isDirectory: true)
        }
        try? fileManager.createDirectory(at: self.root, withIntermediateDirectories: true)
    }

    private func directory(for account: String) -> URL {
        let safe = account.replacingOccurrences(of: "/", with: "_")
        return root.appendingPathComponent(safe, isDirectory: true)
    }

    func load(account: String) -> [AskItem] {
        let url = directory(for: account).appendingPathComponent("asks.json")
        guard let data = try? Data(contentsOf: url) else { return [] }
        return (try? PAJSON.decoder.decode([AskItem].self, from: data)) ?? []
    }

    func save(_ items: [AskItem], account: String) {
        let dir = directory(for: account)
        try? fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        guard let data = try? PAJSON.encoder.encode(items) else { return }
        try? data.write(to: dir.appendingPathComponent("asks.json"), options: .atomic)
    }

    func clear(account: String) {
        try? fileManager.removeItem(at: directory(for: account))
    }
}

// MARK: - 演示仓库（仅用于 Preview / 截图 / 单元测试替身）

actor MockAskRepository: AskRepository {
    private var asks: [String: AskRead]
    private var createTimes: [String: Date] = [:]
    private var counter = 0

    init(seed: [AskRead]) {
        asks = Dictionary(uniqueKeysWithValues: seed.map { ($0.localRunId, $0) })
    }

    static func demo() -> MockAskRepository {
        let scenario = ProcessInfo.processInfo.environment["PA_DEMO_SCENARIO"] ?? "answer"
        return MockAskRepository(seed: DemoPA.seed(scenario: scenario))
    }

    func listAsks(limit: Int) async throws -> [AskRead] {
        asks.values.sorted { $0.createdAt > $1.createdAt }
    }

    func verifyAsks(localRunIDs: [String]) async throws -> [AskVerifyItem] {
        #if DEBUG
        if let scenario = ProcessInfo.processInfo.environment["PA_DEMO_VERIFY"] {
            let classification: AskChatClassification = {
                switch scenario {
                case "not_chat": return .notChat
                case "unknown": return .unknown
                default: return .chat
                }
            }()
            return localRunIDs.map { AskVerifyItem(localRunId: $0, classification: classification) }
        }
        #endif
        return localRunIDs.map { id in
            AskVerifyItem(localRunId: id, classification: asks[id] == nil ? .unknown : .chat)
        }
    }

    func createAsk(text: String, idempotencyKey: String) async throws -> AskCreateResponse {
        if let existing = asks.values.first(where: { $0.requestText == text && $0.status == .pending && false }) {
            return AskCreateResponse(localRunId: existing.localRunId, nexusRunId: nil,
                                     workflowExecutionId: nil, status: existing.status, replayed: true)
        }
        counter += 1
        let id = "demo-ask-\(counter)"
        let now = Date()
        asks[id] = AskRead(
            localRunId: id, nexusRunId: "demo-run-\(counter)", workflowExecutionId: "demo-wf-\(counter)",
            status: .pending, replayed: false, requestText: text, lastEventSequence: 0,
            errorCode: nil, answer: nil, completedAt: nil, createdAt: now
        )
        createTimes[id] = now
        return AskCreateResponse(localRunId: id, nexusRunId: "demo-run-\(counter)",
                                 workflowExecutionId: "demo-wf-\(counter)",
                                 status: .pending, replayed: false)
    }

    func getAsk(id: String) async throws -> AskRead {
        guard var ask = asks[id] else {
            throw PAError(kind: .notFound, status: 404, code: "run_not_found",
                          message: "记录不存在。", retryable: false, requestId: nil, retryAfter: nil)
        }
        guard ask.status != .succeeded, ask.status != .cancelled else { return ask }
        if let started = createTimes[id], Date().timeIntervalSince(started) > 1.5 {
            let updated = AskRead(
                localRunId: ask.localRunId, nexusRunId: ask.nexusRunId,
                workflowExecutionId: ask.workflowExecutionId, status: .succeeded,
                replayed: ask.replayed, requestText: ask.requestText,
                lastEventSequence: ask.lastEventSequence, errorCode: nil,
                answer: DemoPA.answer, completedAt: Date(), createdAt: ask.createdAt
            )
            asks[id] = updated
            return updated
        }
        return ask
    }

    func cancelAsk(id: String) async throws -> AskRead {
        guard var ask = asks[id] else {
            throw PAError(kind: .notFound, status: 404, code: "run_not_found",
                          message: "记录不存在。", retryable: false, requestId: nil, retryAfter: nil)
        }
        let updated = AskRead(
            localRunId: ask.localRunId, nexusRunId: ask.nexusRunId,
            workflowExecutionId: ask.workflowExecutionId, status: .cancelled,
            replayed: ask.replayed, requestText: ask.requestText,
            lastEventSequence: ask.lastEventSequence, errorCode: ask.errorCode,
            answer: ask.answer, completedAt: Date(), createdAt: ask.createdAt
        )
        asks[id] = updated
        return updated
    }
}

enum DemoPA {
    static let answer = """
    咖啡机退款还在处理中。**商家已经发起退款**，但这条通知不是到账凭证。

    ## 还差什么

    1. 找到银行或支付平台的到账记录
    2. 把回执交给助手
    3. 我再按同一订单号核对

    目前没有可匹配的到账回执，所以这笔退款还不能算完成：

    - 只凭金额相同，不能自动关联
    - 没有回执，就不是已到账

    > 收到回执后，我会更新同一件事务，不会新建一条。
    """

    static func seed(scenario: String) -> [AskRead] {
        let now = Date()
        let solved = AskRead(
            localRunId: "demo-solved", nexusRunId: nil, workflowExecutionId: nil,
            status: .succeeded, replayed: false, requestText: "咖啡机退款到哪一步了？",
            lastEventSequence: 3, errorCode: nil, answer: answer,
            completedAt: now.addingTimeInterval(-120), createdAt: now.addingTimeInterval(-180)
        )
        switch scenario {
        case "unknown":
            return [AskRead(localRunId: "demo-unknown", nexusRunId: nil, workflowExecutionId: nil,
                            status: .unknown, replayed: false, requestText: "这周末的旅行准备到哪一步了？",
                            lastEventSequence: 1, errorCode: "nexus_event_stream_incomplete",
                            answer: nil, completedAt: nil, createdAt: now.addingTimeInterval(-60))]
        case "running":
            return [AskRead(localRunId: "demo-running", nexusRunId: nil, workflowExecutionId: nil,
                            status: .running, replayed: false, requestText: "明晚看电影要带什么？",
                            lastEventSequence: 1, errorCode: nil, answer: nil,
                            completedAt: nil, createdAt: now.addingTimeInterval(-20))]
        case "failed":
            return [AskRead(localRunId: "demo-failed", nexusRunId: nil, workflowExecutionId: nil,
                            status: .failed, replayed: false, requestText: "帮我查一下护照到期时间。",
                            lastEventSequence: 1, errorCode: "nexus_unavailable",
                            answer: nil, completedAt: now.addingTimeInterval(-30), createdAt: now.addingTimeInterval(-60))]
        default:
            return [solved]
        }
    }
}
