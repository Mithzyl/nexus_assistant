import Foundation
import SwiftUI

/// 问答状态机：提交、幂等、有限轮询、取消竞态、账号隔离。
/// 视图只读这里的公开状态；网络与会话细节不进入 SwiftUI View。
@MainActor
final class AskStore: ObservableObject {
    @Published private(set) var items: [AskItem] = []
    @Published private(set) var submitting = false
    @Published private(set) var cancelling: Set<String> = []
    @Published var composerError: String?
    @Published private(set) var lastError: String?
    @Published private(set) var needsLogin = false

    let repository: AskRepository
    let localStore: AskLocalStore
    let demoMode: Bool
    private let accountProvider: @MainActor () -> String?

    private var pollTasks: [String: Task<Void, Never>] = [:]
    private var cancellingTasks: [String: Task<Void, Never>] = [:]
    private var currentAccount: String?
    private var historyLoaded = false

    init(repository: AskRepository = AskRepositoryFactory.make(),
         localStore: AskLocalStore = AskLocalStore(),
         demoMode: Bool = AskRepositoryFactory.isDemo,
         accountProvider: (@MainActor () -> String?)? = nil) {
        self.repository = repository
        self.localStore = localStore
        self.demoMode = demoMode
        self.accountProvider = accountProvider ?? { PASessionStore.shared.accountId }
    }

    var activeCount: Int { items.filter(\.isActive).count }

    /// 聊天时间线：最早在上、最新在下。
    /// `items` 内部仍按最新在前保存，便于插入新提问与按 id 定位；
    /// 视图只能读这份时间线，避免两套存储顺序互相错位。
    var timeline: [AskItem] { Array(items.reversed()) }

    // MARK: - 账号

    func bindAccount() async {
        let account = accountProvider()
        if account != currentAccount {
            cancelAllPolling()
            items = account.map { localStore.load(account: $0) } ?? []
            // 上次停在“正在提交”的记录，重启后无法确定是否已到服务器。
            items = items.map { item in
                guard item.phase == .submitting, item.serverId == nil else { return item }
                var copy = item
                copy.phase = .submissionUncertain
                copy.errorMessage = "提交结果暂不确定，可以重试同一次请求。"
                return copy
            }
            currentAccount = account
            historyLoaded = false
            persist()
        }
        needsLogin = account == nil
        guard account != nil else { return }
        await loadHistoryIfNeeded()
        resumePolling()
    }

    func clearForLogout() {
        cancelAllPolling()
        currentAccount = nil
        items = []
        historyLoaded = false
        needsLogin = true
        composerError = nil
        lastError = nil
    }

    // MARK: - 发送

    @discardableResult
    func send(_ text: String) async -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            composerError = "先写下问题。"
            return false
        }
        guard trimmed.count <= 4_000 else {
            composerError = "问题太长，请精简到 4000 字以内。"
            return false
        }
        guard let account = accountProvider() else {
            needsLogin = true
            composerError = "登录后才能向服务器提问，输入已保留。"
            return false
        }
        // 连续点击只允许一个逻辑提问进入提交；在任何 await 之前上锁。
        guard !submitting else { return false }
        submitting = true
        defer { submitting = false }
        composerError = nil

        await bindAccountIfNeeded(account)

        var item = AskItem(question: trimmed)
        items.insert(item, at: 0)
        persist()

        do {
            let response = try await repository.createAsk(text: trimmed, idempotencyKey: item.idempotencyKey)
            guard accountProvider() == account else { return false }
            updateItem(item.id) { current in
                current.serverId = response.localRunId
                current.status = response.status
                current.phase = response.status == .pending ? .queued : .running
                current.errorMessage = nil
                current.updatedAt = Date()
            }
            persist()
            startPolling(itemID: item.id)
            return true
        } catch let error as PAError {
            guard accountProvider() == account else { return false }
            handleSubmissionFailure(error, itemID: item.id, account: account)
            return false
        } catch {
            handleSubmissionFailure(
                PAError(kind: .server, status: nil, code: "transport", message: "请求失败。",
                        retryable: true, requestId: nil, retryAfter: nil),
                itemID: item.id, account: account
            )
            return false
        }
    }

    /// 明确重试同一次提交：复用原幂等键，绝不生成新键。
    func retrySubmission(_ itemID: String) async {
        guard !submitting, let index = index(of: itemID) else { return }
        let item = items[index]
        guard item.serverId == nil else {
            await refresh(itemID)
            return
        }
        guard let account = accountProvider() else {
            needsLogin = true
            return
        }
        submitting = true
        defer { submitting = false }
        updateItem(itemID) { current in
            current.phase = .submitting
            current.errorMessage = nil
            current.updatedAt = Date()
        }
        persist()
        do {
            let response = try await repository.createAsk(text: item.question, idempotencyKey: item.idempotencyKey)
            guard accountProvider() == account else { return }
            updateItem(itemID) { current in
                current.serverId = response.localRunId
                current.status = response.status
                current.phase = response.status == .pending ? .queued : .running
                current.errorMessage = nil
                current.updatedAt = Date()
            }
            persist()
            startPolling(itemID: itemID)
        } catch let error as PAError {
            guard accountProvider() == account else { return }
            handleSubmissionFailure(error, itemID: itemID, account: account)
        } catch {
            handleSubmissionFailure(
                PAError(kind: .server, status: nil, code: "transport", message: "请求失败。",
                        retryable: true, requestId: nil, retryAfter: nil),
                itemID: itemID, account: account
            )
        }
    }

    private func handleSubmissionFailure(_ error: PAError, itemID: String, account: String) {
        switch error.kind {
        case .unauthenticated:
            needsLogin = true
            updateItem(itemID) { item in
                item.phase = .failed
                item.errorMessage = "登录状态已失效，请重新登录后再提问。"
            }
        case .validation, .forbidden, .notFound, .conflict, .cancelled:
            updateItem(itemID) { item in
                item.phase = .failed
                item.errorMessage = error.userMessage
            }
        default:
            // 超时/断网/5xx：请求可能已创建，保留原幂等键，绝不自动换新键重发。
            updateItem(itemID) { item in
                item.phase = .submissionUncertain
                item.errorMessage = "提交结果暂不确定。可以重试同一次请求；不会生成第二次执行。"
            }
        }
        persist()
    }

    // MARK: - 查询与轮询

    func refresh(_ itemID: String) async {
        guard let index = index(of: itemID), let serverId = items[index].serverId else { return }
        let account = accountProvider()
        do {
            let read = try await repository.getAsk(id: serverId)
            guard accountProvider() == account else { return }
            apply(read, to: itemID)
            persist()
        } catch let error as PAError {
            handlePollingError(error, itemID: itemID)
        } catch {
            lastError = "刷新失败，稍后再试。"
        }
    }

    private func startPolling(itemID: String) {
        pollTasks[itemID]?.cancel()
        pollTasks[itemID] = Task { @MainActor [weak self] in
            let delays: [UInt64] = [2, 3, 4, 5, 6, 8, 10]
            for delay in delays {
                if Task.isCancelled { return }
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000)
                if Task.isCancelled { return }
                guard let self else { return }
                let shouldContinue = await self.pollOnce(itemID)
                if !shouldContinue { return }
            }
            self?.markPollingPaused(itemID)
        }
    }

    private func pollOnce(_ itemID: String) async -> Bool {
        guard let index = index(of: itemID), let serverId = items[index].serverId else { return false }
        let account = accountProvider()
        do {
            let read = try await repository.getAsk(id: serverId)
            guard accountProvider() == account else { return false }
            apply(read, to: itemID)
            persist()
            return items.first(where: { $0.id == itemID })?.isActive == true
        } catch let error as PAError {
            return handlePollingError(error, itemID: itemID)
        } catch {
            return true
        }
    }

    @discardableResult
    private func handlePollingError(_ error: PAError, itemID: String) -> Bool {
        switch error.kind {
        case .unauthenticated:
            needsLogin = true
            lastError = "登录状态已失效，请重新登录。"
            pausePolling()
            return false
        case .notFound:
            updateItem(itemID) { item in
                item.phase = .failed
                item.errorMessage = "这条问答在服务器上已不存在。"
            }
            persist()
            return false
        case .rateLimited:
            let wait = Int((error.retryAfter ?? 30).rounded())
            lastError = "请求太频繁，\(wait) 秒后再刷新。"
            markPollingPaused(itemID)
            return false
        case .offline, .timeout, .server:
            // 短暂失败不改变服务器状态，继续按退避重试。
            return true
        default:
            return true
        }
    }

    private func markPollingPaused(_ itemID: String) {
        updateItem(itemID) { item in
            item.pollingPaused = true
        }
        persist()
    }

    // MARK: - 取消

    func cancel(_ itemID: String) async {
        guard let index = index(of: itemID), let serverId = items[index].serverId else { return }
        guard !cancelling.contains(itemID) else { return }
        cancelling.insert(itemID)
        defer { cancelling.remove(itemID) }
        let account = accountProvider()
        do {
            let read = try await repository.cancelAsk(id: serverId)
            guard accountProvider() == account else { return }
            apply(read, to: itemID)
            if read.status == .cancelled {
                pollTasks[itemID]?.cancel()
                pollTasks[itemID] = nil
            }
            persist()
        } catch let error as PAError {
            switch error.kind {
            case .conflict:
                // 服务器认为当前不可取消（例如已完成）：以真实状态为准。
                await refresh(itemID)
                lastError = "这条请求已不可取消，已刷新真实状态。"
            case .unauthenticated:
                needsLogin = true
                lastError = "登录状态已失效，请重新登录。"
            default:
                // 取消结果未知时继续核对状态，不能直接当成功。
                lastError = "取消结果暂不确定，正在继续核对。"
                if let serverId = items[index].serverId {
                    _ = serverId
                }
            }
        } catch {
            lastError = "取消结果暂不确定，正在继续核对。"
        }
    }

    // MARK: - 生命周期

    func pausePolling() {
        cancelAllPolling()
    }

    func resumePolling() {
        guard accountProvider() != nil else { return }
        for item in items where item.isActive {
            if item.serverId != nil, pollTasks[item.id] == nil {
                startPolling(itemID: item.id)
            }
        }
    }

    private func cancelAllPolling() {
        for task in pollTasks.values { task.cancel() }
        pollTasks.removeAll()
        for task in cancellingTasks.values { task.cancel() }
        cancellingTasks.removeAll()
    }

    // MARK: - 历史

    private func loadHistoryIfNeeded() async {
        guard !historyLoaded else { return }
        historyLoaded = true
        do {
            let reads = try await repository.listAsks(limit: 20)
            for read in reads.reversed() {
                if let existing = items.firstIndex(where: { $0.serverId == read.localRunId }) {
                    apply(read, to: items[existing].id)
                } else if !items.contains(where: { $0.question == read.requestText && $0.serverId == nil }) {
                    items.append(AskItem(from: read))
                }
            }
            items.sort { $0.createdAt > $1.createdAt }
            persist()
            for item in items where item.isActive && item.serverId != nil {
                startPolling(itemID: item.id)
            }
        } catch let error as PAError where error.kind == .unauthenticated {
            needsLogin = true
        } catch {
            // 历史失败不影响本地已有记录。
        }
    }

    // MARK: - 应用服务器状态

    private func apply(_ read: AskRead, to itemID: String) {
        guard let index = index(of: itemID) else { return }
        let current = items[index]
        // 已确认取消后，迟到的成功响应不得把界面改回成功。
        if current.phase == .cancelled, read.status != .cancelled { return }
        // 已完成视为锁定，避免乱序响应让同一回答倒退。
        if current.phase == .succeeded, read.status != .succeeded { return }

        var item = current
        item.status = read.status
        item.errorMessage = nil
        item.updatedAt = Date()
        switch read.status {
        case .pending:
            item.phase = .queued
        case .running:
            item.phase = .running
            item.pollingPaused = false
        case .succeeded:
            item.phase = .succeeded
            item.answer = read.answer
            item.completedAt = read.completedAt
            item.pollingPaused = false
        case .failed:
            item.phase = .failed
            item.errorMessage = "服务器未能完成这次处理。"
            item.completedAt = read.completedAt
        case .cancelled:
            item.phase = .cancelled
            item.completedAt = read.completedAt
        case .unknown:
            item.phase = .unknown
            item.errorMessage = "处理状态暂不确定，可以稍后刷新核对。"
        }
        items[index] = item
        if !item.isActive {
            pollTasks[itemID]?.cancel()
            pollTasks[itemID] = nil
        }
    }

    // MARK: - 工具

    private func bindAccountIfNeeded(_ account: String) async {
        if currentAccount != account {
            await bindAccount()
        }
    }

    private func updateItem(_ id: String, _ mutate: (inout AskItem) -> Void) {
        guard let index = index(of: id) else { return }
        var item = items[index]
        mutate(&item)
        items[index] = item
    }

    private func index(of id: String) -> Int? {
        items.firstIndex { $0.id == id }
    }

    private func persist() {
        guard let account = currentAccount else { return }
        localStore.save(items, account: account)
    }
}

extension AskItem {
    init(from read: AskRead) {
        self.init(id: read.localRunId,
                  question: read.requestText,
                  idempotencyKey: "",
                  serverId: read.localRunId,
                  status: read.status,
                  phase: AskItem.phase(for: read.status, answer: read.answer),
                  answer: read.answer,
                  completedAt: read.completedAt,
                  createdAt: read.createdAt,
                  updatedAt: Date(),
                  errorMessage: read.status == .unknown ? "处理状态暂不确定，可以稍后刷新核对。" : nil)
    }

    init(id: String, question: String, idempotencyKey: String, serverId: String?, status: AskStatus?,
         phase: AskPhase, answer: String?, completedAt: Date?, createdAt: Date, updatedAt: Date,
         errorMessage: String?) {
        self.id = id
        self.question = question
        self.idempotencyKey = idempotencyKey
        self.serverId = serverId
        self.status = status
        self.phase = phase
        self.answer = answer
        self.completedAt = completedAt
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.errorMessage = errorMessage
        self.pollingPaused = false
    }

    static func phase(for status: AskStatus, answer: String?) -> AskPhase {
        switch status {
        case .pending: return .queued
        case .running: return .running
        case .succeeded: return .succeeded
        case .failed: return .failed
        case .cancelled: return .cancelled
        case .unknown: return .unknown
        }
    }
}
