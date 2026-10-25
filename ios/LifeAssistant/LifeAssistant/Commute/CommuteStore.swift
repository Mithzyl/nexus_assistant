import Foundation
import SwiftUI

enum CommuteRouteState: Equatable {
    case idle
    case fetching
    case ready(Date)
    case failed(String)

    var isFetching: Bool { self == .fetching }
}

/// 通勤模块的唯一状态源：草稿、已保存配置、最近建议、同步状态与手动刷新轮询。
/// 客户端不做定时判断，也不使用 BGAppRefreshTask；提醒由服务器生成。
@MainActor
final class CommuteStore: ObservableObject {
    @Published private(set) var plan: CommutePlan?
    /// 当前适用的建议（同一计划版本、未过期、服务日期不早于今天）。
    @Published private(set) var advisory: CommuteAdvisory?
    /// 最近一条历史建议：已过期或来自旧计划版本；只用于历史区，不影响当前判断。
    @Published private(set) var historicalAdvisory: CommuteAdvisory?
    /// 从通知/历史区按 id 打开的单条建议；不会替换当前适用建议。
    @Published private(set) var openedAdvisory: CommuteAdvisory?
    @Published private(set) var checkStatus: CheckStatus?
    /// 后端返回的真实阶段；没有该字段时为 nil，只显示状态文字。
    @Published private(set) var checkPhase: CheckPhase?
    /// 独立天气接口的最新快照；与某条建议采用的天气分开保存。
    @Published private(set) var latestWeather: PlanWeather?
    @Published private(set) var weatherLoadedAt: Date?
    @Published private(set) var weatherNotice: String?
    /// 后台运行状态（进程心跳），与当前任务进度分开；失败时保留上次结果。
    @Published private(set) var runtime: CommuteRuntime?
    @Published private(set) var runtimeFetchedAt: Date?
    /// 单调时钟上的读取时刻；页面停留和用户改手机时间都不影响心跳年龄。
    private var runtimeFetchedUptime: TimeInterval?
    @Published private(set) var runtimeFailed = false
    @Published private(set) var runtimeError: String?
    private var runtimeTask: Task<Void, Never>?
    @Published private(set) var syncState: CommuteSyncState = .localDraft
    @Published private(set) var isRefreshing = false
    @Published private(set) var notificationPermission: NotificationPermission = .unknown
    @Published var errorNotice: String?
    @Published var conflictNotice: String?
    /// 设置保存后被置位：提示旧建议不再适用，直到用户重新获取路线并分析。
    @Published private(set) var planUpdatedNotice: String?

    /// 本机 MapKit 采集的路线与交通 ETA；与服务器建议分开保存、分别标明来源。
    @Published private(set) var routeSnapshot: CommuteRouteSnapshot?
    @Published private(set) var routeState: CommuteRouteState = .idle

    let repository: CommuteRepository
    let localStore: CommuteLocalStore
    let routeService: CommuteRouteService
    let demoMode: Bool

    private var pollTask: Task<Void, Never>?
    private var routeTask: Task<Void, Never>?
    private var revisionConflictRetries = 0
    /// 结果未知的提交：保存完整 body 与幂等键；对账时原样复用，不重新查询路线。
    @Published private(set) var pendingSubmission: PendingSubmission?
    private var lastSnapshotDate: Date?
    private var lastLoadAt: Date?
    private(set) var accountID = "guest"
    private var generation = 0

    init(repository: CommuteRepository = CommuteRepositoryFactory.make(),
         localStore: CommuteLocalStore = CommuteLocalStore(),
         routeService: CommuteRouteService = MapKitRouteService(),
         demoMode: Bool = CommuteRepositoryFactory.isDemoBuild) {
        self.repository = repository
        self.localStore = localStore
        self.routeService = routeService
        self.demoMode = demoMode
    }

    var isSignedIn: Bool { demoMode || TokenStore.isSignedIn }

    var hasPlan: Bool { plan != nil }

    var isAdvisoryExpired: Bool { advisory?.isExpired ?? false }

    var cachedAt: Date? { lastSnapshotDate }

    /// 当前适用的判断：计划版本一致、未过期、服务日不是过去。
    static func isApplicable(_ advisory: CommuteAdvisory, plan: CommutePlan,
                             now: Date = Date()) -> Bool {
        // 服务器读取时计算的适用性优先；旧数据没有这些字段时才用本地兜底。
        if let matches = advisory.matchesCurrentPlan, !matches { return false }
        if let expired = advisory.isExpiredFlag, expired { return false }
        if let current = advisory.isCurrentServiceDate, !current { return false }
        guard advisory.planRevision == plan.revision else { return false }
        guard advisory.expiresAt > now else { return false }
        let timezone = TimeZone(identifier: plan.timezone) ?? .current
        if let serviceDate = CommuteDay.parse(advisory.localServiceDate, timezone: timezone),
           CommuteDay.offset(from: serviceDate, timezone: timezone, now: now) < 0 {
            return false
        }
        return true
    }

    func isHistorical(_ advisory: CommuteAdvisory) -> Bool {
        guard let plan else { return true }
        return !Self.isApplicable(advisory, plan: plan)
    }

    /// 分析请求的真实状态；没有具体阶段时不编造百分比或阶段。
    var processingStateText: String? {
        if let pending = pendingSubmission, pending.uncertain {
            return "上次提交结果未知（\(CommuteFormat.dayTime(pending.createdAt))），可核对上次提交"
        }
        // 后端给了真实阶段就用阶段文字，不退回笼统的“等待处理”。
        if let phase = checkPhase, checkStatus == .queued || checkStatus == .running {
            return phase.title
        }
        if let pending = pendingSubmission {
            return "分析请求已提交（\(CommuteFormat.dayTime(pending.createdAt))），等待服务器处理"
        }
        switch checkStatus {
        case .queued:
            // 有真实阶段时用它；没有时只说明已提交，不模拟“正在分析”。
            return checkPhase?.title ?? "已提交，等待处理"
        case .running:
            return checkPhase?.title ?? "正在处理"
        case .unknown:
            return "结果未知，继续核对原任务"
        case .failed: return "上次分析失败"
        case .cancelled: return "上次分析已取消"
        case .succeeded, .degraded, .none: return nil
        }
    }

    /// 仍有需要解释后台状态的任务：排队、处理中或结果未知。
    var hasActiveCheck: Bool {
        if pendingSubmission != nil { return true }
        guard let status = checkStatus else { return false }
        return !status.isTerminal
    }

    struct RuntimeHint: Equatable {
        let text: String
        let detail: String?
        let isFailure: Bool
    }

    /// 只描述“执行程序最近有没有响应”，不改变任务状态，也不承诺任务在前进。
    var runtimeHint: RuntimeHint? {
        runtimeHint(nowUptime: ProcessInfo.processInfo.systemUptime)
    }

    /// 供界面定时重算：nowUptime 来自单调时钟，不用墙钟判断旧缓存。
    func runtimeHint(nowUptime: TimeInterval) -> RuntimeHint? {
        guard hasActiveCheck else { return nil }
        let queried = runtimeFetchedAt.map { CommuteFormat.dayTime($0) }
        guard let runtime else {
            // 尚未发起过查询时不要预先显示“失败”，只显示任务本身。
            guard runtimeFailed else { return nil }
            return RuntimeHint(
                text: "暂时无法获取后台状态。",
                detail: "任务仍已保存，可稍后刷新。",
                isFailure: true
            )
        }
        let elapsed = runtimeFetchedUptime.map { max(0, nowUptime - $0) } ?? 0
        let presence = runtime.workerPresence(elapsedSinceFetch: elapsed)
        let seenText = runtime.worker.lastSeenAt.map { "最近响应：\(CommuteFormat.dayTime($0))" }
        let queryText = queried.map { "查询于 \($0)" }
        let detail = [seenText, queryText].compactMap { $0 }.joined(separator: " · ")

        if runtimeFailed {
            return RuntimeHint(
                text: "暂时无法获取后台状态。",
                detail: detail + "。这是上次查询的结果；任务仍已保存。",
                isFailure: true
            )
        }
        switch presence.state {
        case .notSeen:
            return RuntimeHint(text: "尚未收到后台执行程序的响应。",
                               detail: detail + "。任务仍已保存。",
                               isFailure: false)
        case .stale:
            return RuntimeHint(text: "后台执行程序近期未响应，任务仍已保存。",
                               detail: detail,
                               isFailure: false)
        case .online:
            return RuntimeHint(text: "后台执行程序最近有响应。",
                               detail: detail + "。心跳正常不代表这条任务正在推进。",
                               isFailure: false)
        }
    }

    /// 详情/回前台读取；同一时刻共享一个请求，避免和进度轮询各起一个高频计时器。
    func refreshRuntime(force: Bool = false) async {
        guard isSignedIn, hasActiveCheck else { return }
        if let task = runtimeTask {
            await task.value
            return
        }
        if !force, let fetched = runtimeFetchedUptime,
           ProcessInfo.processInfo.systemUptime - fetched < 15 {
            return
        }
        let requestGeneration = generation
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.loadRuntime(generation: requestGeneration)
        }
        runtimeTask = task
        await task.value
        runtimeTask = nil
    }

    private func loadRuntime(generation: Int) async {
        do {
            let snapshot = try await repository.runtime()
            guard generation == self.generation else { return }
            runtime = snapshot
            runtimeFetchedAt = Date()
            runtimeFetchedUptime = ProcessInfo.processInfo.systemUptime
            runtimeFailed = false
            runtimeError = nil
        } catch let error as CommuteAPIError {
            guard generation == self.generation else { return }
            // 心跳读取失败不是任务失败；也不触发全局登出或新建检查。
            runtimeFailed = true
            runtimeError = error.kind == .unauthenticated
                ? "登录状态需要重新确认。"
                : "暂时无法获取后台状态。"
        } catch {
            guard generation == self.generation else { return }
            runtimeFailed = true
            runtimeError = "暂时无法获取后台状态。"
        }
    }

    private func applyLatest(_ latest: CommuteAdvisory) {
        guard let plan else {
            advisory = nil
            historicalAdvisory = latest
            return
        }
        if Self.isApplicable(latest, plan: plan) {
            advisory = latest
            // 新分析已经产出匹配当前计划的结果，旧设置提示可以收起。
            planUpdatedNotice = nil
        } else {
            // 旧响应不能覆盖当前适用建议；只进历史区。
            historicalAdvisory = latest
        }
    }

    // MARK: - 读取

    func setAccount(_ raw: String?) {
        let next = raw ?? "guest"
        guard next != accountID else { return }
        generation += 1
        pollTask?.cancel()
        pollTask = nil
        accountID = next
        plan = nil
        advisory = nil
        historicalAdvisory = nil
        openedAdvisory = nil
        planUpdatedNotice = nil
        routeSnapshot = nil
        routeState = .idle
        routeTask?.cancel()
        routeTask = nil
        pendingSubmission = nil
        checkStatus = nil
        checkPhase = nil
        latestWeather = nil
        weatherLoadedAt = nil
        weatherNotice = nil
        runtime = nil
        runtimeFetchedAt = nil
        runtimeFetchedUptime = nil
        runtimeFailed = false
        runtimeError = nil
        runtimeTask?.cancel()
        runtimeTask = nil
        lastLoadAt = nil
        lastSnapshotDate = nil
        syncState = .localDraft
    }

    /// 回到前台/下拉时按缓存有效期刷新；不在 body 计算里发请求。
    func refreshIfStale(maxAge: TimeInterval = 15 * 60) async {
        guard isSignedIn else { return }
        if let last = lastLoadAt, Date().timeIntervalSince(last) < maxAge,
           advisory != nil, !isAdvisoryExpired { return }
        await reloadFromServer()
    }

    func load() async {
        // 演示构建每次从固定 mock 开始，不继承上次演示产生的快照/待核对记录。
        if !demoMode, let snapshot = localStore.loadSnapshot(account: accountID) {
            plan = snapshot.plans.first
            advisory = snapshot.advisory
            historicalAdvisory = snapshot.historicalAdvisory
            if let plan, let restored = snapshot.advisory,
               !Self.isApplicable(restored, plan: plan) {
                historicalAdvisory = restored
                advisory = nil
            }
            lastSnapshotDate = snapshot.fetchedAt
            syncState = .offline(snapshot.fetchedAt)
        } else if let draft = localStore.loadDraft(), plan == nil {
            syncState = .localDraft
            _ = draft
        }
        // 演示默认从固定 mock 开始；UI 验证重开恢复时用 COMMUTE_DEMO_KEEP_PENDING=1 读取原记录。
        let keepDemoPending = ProcessInfo.processInfo.environment["COMMUTE_DEMO_KEEP_PENDING"] == "1"
        pendingSubmission = (!demoMode || keepDemoPending)
            ? localStore.loadPending(account: accountID) : nil
        if pendingSubmission?.uncertain == true {
            errorNotice = "上次提交结果未知，可点“核对上次提交”复用同一请求。"
        }

        guard isSignedIn else {
            if plan == nil { syncState = .localDraft } else if lastSnapshotDate == nil { syncState = .needsLogin }
            if !isSignedIn { syncState = .needsLogin }
            return
        }
        await reloadFromServer()
        // 重开 App 时继续查询原来的检查，不创建新的分析。
        if pendingSubmission?.checkId != nil, pendingSubmission?.uncertain == false {
            await resumeProgressIfNeeded()
        }
    }

    func reloadFromServer() async {
        let requestGeneration = generation
        syncState = .syncing
        do {
            let plans = try await repository.listPlans()
            guard requestGeneration == generation else { return }
            plan = plans.first
            if let plan {
                do {
                    let latest = try await repository.latestAdvisory(planId: plan.id)
                    guard requestGeneration == generation else { return }
                    applyLatest(latest)
                } catch let error as CommuteAPIError where error.code == "NO_ADVISORY" {
                    advisory = nil
                }
                syncState = .synced(Date())
                lastLoadAt = Date()
                // 独立天气失败不影响建议读取；两者更新时间可以不同。
                await loadLatestWeather(planId: plan.id, generation: requestGeneration)
                saveSnapshot()
            } else {
                advisory = nil
                syncState = .localDraft
            }
        } catch {
            handle(error, fallbackDate: lastSnapshotDate)
        }
    }

    /// 只读刷新独立天气；不创建 check，也不改动建议。
    func refreshWeather() async {
        guard isSignedIn, let plan else { return }
        let requestGeneration = generation
        await loadLatestWeather(planId: plan.id, generation: requestGeneration)
    }

    private func loadLatestWeather(planId: String, generation: Int) async {
        do {
            let weather = try await repository.latestWeather(planId: planId, localServiceDate: nil)
            guard generation == self.generation else { return }
            latestWeather = weather
            weatherLoadedAt = Date()
            weatherNotice = nil
        } catch let error as CommuteAPIError where error.code == "NO_WEATHER" {
            guard generation == self.generation else { return }
            latestWeather = nil
            weatherNotice = "服务器还没有独立天气记录。"
        } catch {
            guard generation == self.generation else { return }
            weatherNotice = "天气暂时读取失败，保留已有天气。"
        }
    }

    // MARK: - 保存设置

    func save(draft: CommutePlanDraft) async -> Bool {
        localStore.saveDraft(draft)
        guard isSignedIn else {
            syncState = .needsLogin
            errorNotice = "未登录，设置只保存在本地草稿。登录后可启用服务器提醒。"
            return false
        }
        syncState = .syncing
        do {
            let saved: CommutePlan
            if let existing = plan {
                saved = try await repository.updatePlan(id: existing.id, draft: draft, revision: existing.revision)
            } else {
                saved = try await repository.createPlan(draft)
            }
            let previous = plan
            plan = saved
            if let previous, previous.revision != saved.revision {
                // 新 revision 意味着“旧建议不适用于新设置”：转入历史区，等待明确的新分析。
                if let current = advisory {
                    historicalAdvisory = current
                    advisory = nil
                }
                // 旧快照与旧幂等键都不能用于新计划；旧任务的进度和后台状态也不再属于当前设置。
                clearPending()
                checkStatus = nil
                checkPhase = nil
                runtime = nil
                runtimeFetchedAt = nil
                runtimeFetchedUptime = nil
                runtimeFailed = false
                runtimeError = nil
                planUpdatedNotice = "设置已更新：旧建议不适用于新计划，请重新获取路线并分析。"
            }
            syncState = .synced(Date())
            conflictNotice = nil
            errorNotice = nil
            saveSnapshot()
            return true
        } catch let error as CommuteAPIError {
            if error.kind == .conflict {
                conflictNotice = error.userMessage
            }
            handle(error, fallbackDate: lastSnapshotDate)
            return false
        } catch {
            handle(error, fallbackDate: lastSnapshotDate)
            return false
        }
    }

    /// 明确保存为本地草稿；不冒充服务器已保存，也不覆盖远端。
    func saveLocalDraft(_ draft: CommutePlanDraft) {
        localStore.saveDraft(draft)
        syncState = .localDraft
        conflictNotice = nil
        errorNotice = "已保存为本地草稿；登录服务器后才会生成提醒。"
    }

    @discardableResult
    func deleteCurrentPlan() async -> Bool {
        guard let plan else { return false }
        guard isSignedIn else {
            errorNotice = "未登录服务器，无法删除远端配置。"
            return false
        }
        do {
            try await repository.deletePlan(id: plan.id)
            self.plan = nil
            advisory = nil
            historicalAdvisory = nil
            planUpdatedNotice = nil
            checkStatus = nil
            checkPhase = nil
            runtime = nil
            runtimeFetchedAt = nil
            runtimeFetchedUptime = nil
            runtimeFailed = false
            runtimeError = nil
            clearPending()
            localStore.clear()
            syncState = .localDraft
            return true
        } catch {
            handle(error, fallbackDate: lastSnapshotDate)
            return false
        }
    }

    // MARK: - 路线 + 智能分析

    /// 显式操作：先用 MapKit 取路线与交通 ETA，再按实际契约提交一次检查。
    /// 一次提交固定 body 与幂等键；结果未知时保存原样记录，核对时复用同一份 body。
    func updateRouteAndAnalyze() async {
        guard !isRefreshing, !routeState.isFetching else { return }
        guard let plan else {
            errorNotice = "先保存一条通勤设置。"
            return
        }
        guard isSignedIn else {
            syncState = .needsLogin
            errorNotice = "未登录，无法向服务器请求建议。"
            return
        }
        if pendingSubmission?.uncertain == true {
            errorNotice = "上次提交结果未知。请先“核对上次提交”，避免重复生成分析。"
            return
        }
        let requestGeneration = generation
        routeTask?.cancel()
        routeTask = nil
        routeState = .fetching
        let arrival = advisory?.targetArrivalAt ?? Self.nextArrivalDate(for: plan)
        let snapshot: CommuteRouteSnapshot
        do {
            snapshot = try await routeService.fetchRoute(
                origin: plan.origin,
                destination: plan.destination,
                mode: plan.transportMode,
                arrivalBy: arrival
            )
            guard requestGeneration == generation else { return }
            routeSnapshot = snapshot
            routeState = .ready(Date())
        } catch is CancellationError {
            if requestGeneration == generation { routeState = .idle }
            return
        } catch {
            guard requestGeneration == generation else { return }
            if let routeError = error as? CommuteRouteError {
                routeState = .failed(routeError.errorDescription ?? "路线查询失败。")
            } else {
                routeState = .failed("路线查询失败，稍后可重试。")
            }
            // 没有快照就不能提交路线 AI 分析；服务器仍可按计划生成天气/无路线降级建议。
            errorNotice = "路线查询失败：本次只能请求服务器按计划生成建议，无法提交路线快照。"
            return
        }
        guard requestGeneration == generation else { return }
        await submit(snapshot.submission(plan: plan), planId: plan.id,
                     generation: requestGeneration, isRetry: false, key: nil)
    }

    /// 核对/重试同一次提交：幂等键与 body 原样复用，服务器重放原检查，不新建分析。
    func reconcilePendingSubmission() async {
        guard !isRefreshing, !routeState.isFetching else { return }
        guard let pending = pendingSubmission else { return }
        guard isSignedIn else { syncState = .needsLogin; return }
        await submit(pending.body, planId: pending.planId, generation: generation,
                     isRetry: true, key: pending.key)
    }

    private func submit(_ body: CheckCreateRequest?, planId: String, generation: Int,
                        isRetry: Bool, key proposedKey: String?) async {
        guard !isRefreshing else { return }
        let key = proposedKey ?? UUID().uuidString
        if !isRetry {
            let pending = PendingSubmission(
                planId: planId,
                revision: body?.planRevision ?? plan?.revision ?? 0,
                key: key,
                body: body,
                createdAt: Date(),
                uncertain: false,
                accountId: accountID
            )
            pendingSubmission = pending
            localStore.savePending(pending)
        }
        pollTask?.cancel()
        isRefreshing = true
        checkStatus = .queued
        defer { isRefreshing = false }
        do {
            let check = try await repository.requestCheck(planId: planId, idempotencyKey: key,
                                                          request: body)
            guard generation == self.generation else { return }
            revisionConflictRetries = 0
            checkStatus = check.status
            checkPhase = check.phase
            if var pending = pendingSubmission {
                pending.checkId = check.id
                pendingSubmission = pending
                localStore.savePending(pending)
            }
            if let advisoryId = check.advisoryId, let latest = try? await repository.advisory(id: advisoryId) {
                advisory = latest
                syncState = .synced(Date())
                clearPending()
                saveSnapshot()
                return
            }
            // 只把轮询放进可取消的 pollTask；请求本身不被后台切换取消。
            let task = Task { @MainActor [weak self] in
                guard let self else { return }
                await self.poll(planId: planId, first: check)
            }
            pollTask = task
            await task.value
        } catch {
            guard generation == self.generation else { return }
            handleSubmissionFailure(error, planId: planId, generation: generation)
        }
    }

    private func handleSubmissionFailure(_ error: Error, planId: String, generation: Int) {
        guard let apiError = error as? CommuteAPIError else {
            markSubmissionUncertain()
            handle(error, fallbackDate: lastSnapshotDate)
            return
        }
        switch apiError.code {
        case "plan_revision_conflict":
            clearPending()
            guard revisionConflictRetries < 1 else {
                revisionConflictRetries = 0
                errorNotice = "计划仍在更新，已停止自动重试；请稍后重新更新路线。"
                return
            }
            revisionConflictRetries += 1
            errorNotice = "计划已更新，正在重新读取计划；旧快照不会沿用。"
            Task { @MainActor [weak self] in
                guard let self else { return }
                await self.reloadFromServer()
                guard generation == self.generation else { return }
                // 只在新计划上重新查询路线并创建新提交，不复用旧 body 或旧键。
                await self.updateRouteAndAnalyze()
            }
        case "idempotency_key_reused":
            clearPending()
            errorNotice = "同一次提交的幂等键与请求体不一致，已停止自动重试；请重新更新路线并分析。"
        case "route_snapshot_origin_mismatch", "route_snapshot_destination_mismatch",
             "route_snapshot_mode_mismatch", "route_snapshot_clock_skew", "route_snapshot_stale",
             "route_snapshot_window_invalid", "route_snapshot_query_invalid":
            clearPending()
            errorNotice = "服务器未接受路线快照：\(apiError.message)请重新更新路线后再分析。"
        case "check_window_closed", "skipped_today", "plan_disabled":
            clearPending()
            errorNotice = apiError.message
        default:
            switch apiError.kind {
            case .timeout, .offline, .server, .rateLimited:
                markSubmissionUncertain()
            case .unauthenticated:
                clearPending()
                syncState = .needsLogin
                errorNotice = apiError.userMessage
            default:
                clearPending()
                errorNotice = apiError.userMessage
            }
            handle(error, fallbackDate: lastSnapshotDate)
        }
    }

    private func markSubmissionUncertain() {
        guard var pending = pendingSubmission else { return }
        pending.uncertain = true
        pendingSubmission = pending
        localStore.savePending(pending)
        errorNotice = "提交结果未知：可稍后核对上次提交，不会自动重复分析。"
    }

    private func clearPending() {
        pendingSubmission = nil
        localStore.savePending(nil)
    }

    /// 计划的“下一次到达时间”：按计划时区算，已过则顺延一天。仅用于本机 MapKit 查询。
    static func nextArrivalDate(for plan: CommutePlan, now: Date = Date()) -> Date? {
        let timezone = TimeZone(identifier: plan.timezone) ?? .current
        let parts = plan.arrivalLocalTime.split(separator: ":").compactMap { Int($0) }
        guard parts.count >= 2 else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timezone
        guard let today = calendar.date(bySettingHour: parts[0], minute: parts[1], second: 0, of: now) else {
            return nil
        }
        return today > now ? today : calendar.date(byAdding: .day, value: 1, to: today)
    }

    // MARK: - 手动刷新与轮询

    /// 只重新读取服务器已有内容：不创建 check，也不触发模型。
    /// 页面进入、下拉刷新、返回和重启都走这条只读路径。
    func refresh(planId: String? = nil) async {
        guard isSignedIn else {
            syncState = .needsLogin
            return
        }
        _ = planId
        await reloadFromServer()
    }

    /// 有限轮询 + 退避；页面离开/进后台时取消本机观察，服务器任务继续执行。
    private func poll(planId: String, first: CommuteCheck) async {
        let delays: [UInt64] = [1_000, 2_000, 3_000, 5_000, 8_000]
        var current = first
        var transientFailures = 0
        for delay in delays {
            if current.status.isTerminal { break }
            let factor = UInt64(1 << min(transientFailures, 2))
            do {
                try await Task.sleep(nanoseconds: delay * factor * 1_000_000)
            } catch {
                return
            }
            if Task.isCancelled { return }
            do {
                current = try await repository.check(id: current.id)
                transientFailures = 0
                checkStatus = current.status
                checkPhase = current.phase
            } catch {
                guard !Task.isCancelled else { return }
                transientFailures += 1
                if transientFailures >= 2 {
                    errorNotice = "暂时无法查询处理状态；任务已保留，稍后可刷新核对。"
                    return
                }
                continue
            }
        }

        guard current.status.isTerminal else {
            checkStatus = .running
            errorNotice = "服务器仍在处理，稍后回到页面可查看最新建议。"
            return
        }
        if current.status == .failed {
            // A failed check may still have persisted a truthful "unavailable"
            // advisory. Adopt it if present rather than showing a blank card.
            if current.advisoryId != nil {
                await adopt(advisoryId: current.advisoryId, planId: planId)
            }
            clearPending()
            syncState = .failed(current.errorMessage ?? "服务器未能生成建议。",
                                retryable: current.error?.retryable ?? true)
            errorNotice = current.errorMessage ?? "服务器未能生成建议，可稍后重试。"
            return
        }
        await adopt(advisoryId: current.advisoryId, planId: planId)
    }

    private func adopt(advisoryId: String?, planId: String) async {
        do {
            let latest: CommuteAdvisory
            if let advisoryId {
                latest = try await repository.advisory(id: advisoryId)
            } else {
                latest = try await repository.latestAdvisory(planId: planId)
            }
            applyLatest(latest)
            syncState = .synced(Date())
            clearPending()
            saveSnapshot()
        } catch let error as CommuteAPIError where error.code == "NO_ADVISORY" {
            advisory = nil
            clearPending()
            errorNotice = "本次没有生成建议。"
        } catch {
            handle(error, fallbackDate: lastSnapshotDate)
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    /// 进后台暂停本机轮询；服务器任务不受影响。
    func pausePolling() {
        stopPolling()
    }

    /// 回前台继续查询原来的检查，不创建新的分析。
    func resumeProgressIfNeeded() async {
        guard let plan else { return }
        guard let checkId = pendingSubmission?.checkId,
              pendingSubmission?.uncertain == false else { return }
        if let status = checkStatus, status.isTerminal { return }
        let requestGeneration = generation
        do {
            let check = try await repository.check(id: checkId)
            guard requestGeneration == generation else { return }
            checkStatus = check.status
            checkPhase = check.phase
            if check.status.isTerminal {
                await adopt(advisoryId: check.advisoryId, planId: plan.id)
            } else {
                let task = Task { @MainActor [weak self] in
                    guard let self else { return }
                    await self.poll(planId: plan.id, first: check)
                }
                pollTask = task
            }
        } catch {
            errorNotice = "暂时无法查询处理状态；任务已保留，稍后可刷新核对。"
        }
    }


    // MARK: - 通知点击 / 动作

    func loadAdvisory(id: String) async {
        do {
            let loaded = try await repository.advisory(id: id)
            openedAdvisory = loaded
            plan = plan ?? planFor(loaded)
            // 历史/旧版本建议只进历史区；不能覆盖当前适用建议。
            if let plan, Self.isApplicable(loaded, plan: plan) {
                advisory = loaded
            } else {
                historicalAdvisory = loaded
            }
            syncState = .synced(Date())
            saveSnapshot()
        } catch let error as CommuteAPIError where error.status == 404 {
            errorNotice = "这条建议已不存在或已被删除。"
        } catch {
            handle(error, fallbackDate: lastSnapshotDate)
        }
    }

    func reportOpened(advisoryId: String) async {
        guard isSignedIn else { return }
        try? await repository.reportAction(
            advisoryId: advisoryId, action: "opened",
            installationId: CommuteConfig.installationID, idempotencyKey: UUID().uuidString
        )
    }

    func skipToday() async {
        guard let advisory, isSignedIn else { return }
        do {
            try await repository.reportAction(
                advisoryId: advisory.id, action: "skip_today",
                installationId: CommuteConfig.installationID, idempotencyKey: UUID().uuidString
            )
            errorNotice = "已跳过今天这条提醒。"
        } catch {
            handle(error, fallbackDate: lastSnapshotDate)
        }
    }

    // MARK: - 设备通知

    func uploadPushToken(_ token: String) async {
        guard isSignedIn else { return }
        do {
            try await repository.registerDevice(
                installationId: CommuteConfig.installationID,
                token: token,
                permission: notificationPermission.rawValue
            )
        } catch {
            // 注册失败不阻断使用；准确显示为未知，而不是“提醒已开启”。
            notificationPermission = .unknown
        }
    }

    func unregisterDevice() async {
        guard isSignedIn else { return }
        try? await repository.unregisterDevice(installationId: CommuteConfig.installationID)
    }

    func updatePermission(_ permission: NotificationPermission) {
        notificationPermission = permission
    }

    // MARK: - 登录

    @discardableResult
    func login(email: String, password: String) async -> Bool {
        // Authentication belongs to the shared PA session, not the commute module.
        let ok = await PASessionStore.shared.login(email: email, password: password)
        guard ok else { return false }
        conflictNotice = nil
        await reloadFromServer()
        return true
    }

    func logout() {
        // The PA session store owns the shared bearer token. A commute-module
        // logout must not delete another module's credential.
        syncState = .needsLogin
    }

    // MARK: - 私有

    private func planFor(_ advisory: CommuteAdvisory) -> CommutePlan? {
        guard let plan, plan.id == advisory.planId else { return plan }
        return plan
    }

    private func saveSnapshot() {
        guard let plan else { return }
        let snapshot = CommuteLocalStore.Snapshot(
            plans: [plan], advisory: advisory, historicalAdvisory: historicalAdvisory,
            fetchedAt: Date(), accountId: accountID
        )
        localStore.saveSnapshot(snapshot)
        lastSnapshotDate = snapshot.fetchedAt
    }

    private func handle(_ error: Error, fallbackDate: Date?) {
        if Task.isCancelled { return }
        guard let apiError = error as? CommuteAPIError else {
            errorNotice = "请求失败，稍后可重试。"
            syncState = .failed("请求失败", retryable: true)
            return
        }
        switch apiError.kind {
        case .unauthenticated:
            // 401 for a commute request means this request is unauthorized; it
            // does not prove the shared PA session is revoked. Never delete the
            // credential here — PASessionStore owns its lifecycle.
            syncState = .needsLogin
            errorNotice = apiError.userMessage
        case .offline, .timeout:
            if let fallbackDate {
                syncState = .offline(fallbackDate)
            } else {
                syncState = .failed(apiError.userMessage, retryable: apiError.retryable)
            }
            errorNotice = apiError.userMessage
        case .rateLimited:
            errorNotice = apiError.userMessage
            syncState = .failed(apiError.userMessage, retryable: true)
        case .cancelled:
            return
        default:
            errorNotice = apiError.userMessage
            syncState = .failed(apiError.userMessage, retryable: apiError.retryable)
        }
    }
}
