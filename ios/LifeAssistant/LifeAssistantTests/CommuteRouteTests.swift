import XCTest
@testable import LifeAssistant

final class RecordingCommuteRepository: CommuteRepository {
    var plans: [CommutePlan] = []
    var requestedKeys: [String] = []
    var requestedBodies: [CheckCreateRequest?] = []
    var listPlansCalls = 0
    var checkResult: Result<CommuteCheck, Error> = .success(
        CommuteCheck(id: "check-1", status: .succeeded, advisoryId: nil, errorCode: nil, errorMessage: nil)
    )
    var latestError: Error = CommuteAPIError(kind: .notFound, status: 404, code: "NO_ADVISORY",
                                             message: "no advisory", retryable: false,
                                             requestId: nil)
    var runtimeResult: Result<CommuteRuntime, Error> = .failure(
        CommuteAPIError(kind: .server, status: 503, code: "runtime_unavailable",
                        message: "no runtime", retryable: true, requestId: nil)
    )
    var runtimeDelay: TimeInterval = 0

    func login(email: String, password: String) async throws -> LoginResponse { throw latestError }
    func listPlans() async throws -> [CommutePlan] { listPlansCalls += 1; return plans }
    func createPlan(_ draft: CommutePlanDraft) async throws -> CommutePlan { throw latestError }
    func updatePlan(id: String, draft: CommutePlanDraft, revision: Int) async throws -> CommutePlan {
        guard let index = plans.firstIndex(where: { $0.id == id }),
              plans[index].revision == revision else { throw latestError }
        var updated = plans[index]
        updated.revision = revision + 1
        updated.name = draft.name
        plans[index] = updated
        return updated
    }
    func deletePlan(id: String) async throws { throw latestError }
    func requestCheck(planId: String, idempotencyKey: String,
                      request: CheckCreateRequest?) async throws -> CommuteCheck {
        requestedKeys.append(idempotencyKey)
        requestedBodies.append(request)
        return try checkResult.get()
    }
    func check(id: String) async throws -> CommuteCheck { throw latestError }
    func latestAdvisory(planId: String) async throws -> CommuteAdvisory { throw latestError }
    func latestWeather(planId: String, localServiceDate: String?) async throws -> PlanWeather { throw latestError }
    func runtime() async throws -> CommuteRuntime {
        if runtimeDelay > 0 { try? await Task.sleep(nanoseconds: UInt64(runtimeDelay * 1_000_000_000)) }
        return try runtimeResult.get()
    }
    func advisory(id: String) async throws -> CommuteAdvisory { throw latestError }
    func registerDevice(installationId: String, token: String, permission: String) async throws { throw latestError }
    func unregisterDevice(installationId: String) async throws { throw latestError }
    func reportAction(advisoryId: String, action: String, installationId: String, idempotencyKey: String) async throws {
        throw latestError
    }
}

final class FakeRouteService: CommuteRouteService {
    var calls = 0
    var delayNanoseconds: UInt64 = 0
    var lastArrivalBy: Date?
    var result: Result<CommuteRouteSnapshot, Error> = .success(CommuteRouteSnapshot(
        provider: "mapkit",
        transport: "automobile",
        queriedAt: Date(),
        targetArrivalAt: nil,
        etaSeconds: 1_500,
        etaArrivalAt: nil,
        etaDepartureAt: nil,
        etaDistanceMeters: 8_600,
        primaryRouteName: "路线 A",
        primaryRouteDistanceMeters: 8_900,
        candidates: [CommuteRouteCandidate(name: "路线 A", distanceMeters: 8_900,
                                           routeExpectedTravelSeconds: 1_400, pointCount: 120)]
    ))

    func fetchRoute(origin: GeoPoint, destination: GeoPoint, mode: TransportMode,
                    arrivalBy: Date?) async throws -> CommuteRouteSnapshot {
        calls += 1
        lastArrivalBy = arrivalBy
        if delayNanoseconds > 0 { try? await Task.sleep(nanoseconds: delayNanoseconds) }
        var snapshot = try result.get()
        snapshot = CommuteRouteSnapshot(
            provider: snapshot.provider, transport: snapshot.transport,
            queriedAt: Date(), targetArrivalAt: arrivalBy,
            etaSeconds: snapshot.etaSeconds, etaArrivalAt: snapshot.etaArrivalAt,
            etaDepartureAt: snapshot.etaDepartureAt,
            etaDistanceMeters: snapshot.etaDistanceMeters,
            primaryRouteName: snapshot.primaryRouteName,
            primaryRouteDistanceMeters: snapshot.primaryRouteDistanceMeters,
            candidates: snapshot.candidates
        )
        return snapshot
    }
}

@MainActor
final class CommuteRouteTests: XCTestCase {
    private var repository: RecordingCommuteRepository!
    private var routeService: FakeRouteService!
    private var store: CommuteStore!
    private var directory: URL!

    private let plan = CommutePlan(
        id: "plan-1", name: "上班", enabled: true, timezone: "Asia/Shanghai",
        weekdays: [2], arrivalLocalTime: "09:00", checkLocalTime: "08:00",
        transportMode: .driving, bufferMinutes: 10, rainProbabilityThreshold: 0.5,
        rainExtraBufferMinutes: 10, notifyEnabled: true,
        origin: GeoPoint(label: "家", latitude: 30.6, longitude: 104.0),
        destination: GeoPoint(label: "公司", latitude: 30.65, longitude: 104.06),
        revision: 3, createdAt: Date(), updatedAt: Date()
    )

    override func setUp() async throws {
        try await super.setUp()
        repository = RecordingCommuteRepository()
        repository.plans = [plan]
        routeService = FakeRouteService()
        directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("commute-route-tests-" + UUID().uuidString)
        store = CommuteStore(repository: repository,
                             localStore: CommuteLocalStore(directory: directory),
                             routeService: routeService,
                             demoMode: true)
        store.setAccount("user-1")
        await store.load()
    }

    private func waitUntil(timeout: TimeInterval = 2,
                           _ condition: @escaping () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 30_000_000)
        }
    }

    func testNextArrivalDateUsesPlanTimezone() throws {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(secondsFromGMT: 0)!
        let morning = try XCTUnwrap(utc.date(from: DateComponents(year: 2026, month: 10, day: 24, hour: 0, minute: 0)))
        let target = try XCTUnwrap(CommuteStore.nextArrivalDate(for: plan, now: morning))
        var shanghai = Calendar(identifier: .gregorian)
        shanghai.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        XCTAssertEqual(utc.dateComponents(in: shanghai.timeZone, from: target).hour, 9)
        XCTAssertEqual(utc.dateComponents(in: shanghai.timeZone, from: target).day, 24)

        let afternoon = try XCTUnwrap(utc.date(from: DateComponents(year: 2026, month: 10, day: 24, hour: 2, minute: 0)))
        let tomorrow = try XCTUnwrap(CommuteStore.nextArrivalDate(for: plan, now: afternoon))
        XCTAssertEqual(utc.dateComponents(in: shanghai.timeZone, from: tomorrow).day, 25)
    }

    func testSubmitsPlanRevisionAndMapKitSnapshot() async throws {
        await store.updateRouteAndAnalyze()

        let body = try XCTUnwrap(repository.requestedBodies.first ?? nil)
        XCTAssertEqual(body.planRevision, 3)
        XCTAssertEqual(body.routeSnapshot.provider, "mapkit_client")
        XCTAssertEqual(body.routeSnapshot.transportMode, "driving")
        XCTAssertEqual(body.routeSnapshot.queryType, "arrival_deadline")
        XCTAssertNotNil(body.routeSnapshot.requestedArrivalAt)
        XCTAssertNil(body.routeSnapshot.requestedDepartureAt)
        XCTAssertEqual(body.routeSnapshot.etaSource, "calculate_eta")
        XCTAssertEqual(body.routeSnapshot.etaScope, "overall")
        XCTAssertEqual(body.routeSnapshot.durationInTrafficSeconds, 1_500)
        XCTAssertEqual(body.routeSnapshot.distanceMeters, 8_600)
        XCTAssertEqual(body.routeSnapshot.candidateRoutes.count, 1)
        XCTAssertEqual(body.routeSnapshot.candidateRoutes[0].durationSeconds, 1_400)
        XCTAssertNil(body.routeSnapshot.candidateRoutes[0].durationInTrafficSeconds,
                     "候选路线不能共用总体 ETA")
        XCTAssertEqual(body.routeSnapshot.origin.latitude, plan.origin.latitude)
        XCTAssertEqual(routeService.lastArrivalBy, body.routeSnapshot.requestedArrivalAt)
    }

    func testDuplicateTapFetchesAndSubmitsOnce() async throws {
        routeService.delayNanoseconds = 150_000_000

        async let first: Void = store.updateRouteAndAnalyze()
        async let second: Void = store.updateRouteAndAnalyze()
        _ = await (first, second)

        XCTAssertEqual(routeService.calls, 1, "重复点击只能获取一次路线")
        XCTAssertEqual(repository.requestedKeys.count, 1, "重复点击只能提交一次检查")
    }

    func testUncertainSubmissionIsReconciledWithIdenticalBodyAndKey() async throws {
        repository.checkResult = .failure(CommuteAPIError(kind: .timeout, status: nil, code: "timeout",
                                                           message: "timeout", retryable: true, requestId: nil))
        await store.updateRouteAndAnalyze()

        let pending = try XCTUnwrap(store.pendingSubmission)
        XCTAssertTrue(pending.uncertain)
        XCTAssertEqual(repository.requestedKeys.count, 1)
        XCTAssertTrue(store.processingStateText?.contains("未知") == true,
                      "结果未知时不能显示成正在处理")

        // 重新点“更新路线并分析”不会重新查询/提交。
        await store.updateRouteAndAnalyze()
        XCTAssertEqual(repository.requestedKeys.count, 1, "结果未知时不允许静默重复分析")

        repository.checkResult = .success(
            CommuteCheck(id: "check-1", status: .succeeded, advisoryId: nil, errorCode: nil, errorMessage: nil)
        )
        await store.reconcilePendingSubmission()

        XCTAssertEqual(repository.requestedKeys.count, 2)
        XCTAssertEqual(repository.requestedKeys[0], repository.requestedKeys[1], "核对必须复用同一幂等键")
        XCTAssertEqual(repository.requestedBodies[0], repository.requestedBodies[1], "核对必须复用完全相同的 body")
        XCTAssertNil(store.pendingSubmission)
    }

    func testRevisionConflictReloadsPlanAndRequeriesRoute() async throws {
        repository.checkResult = .failure(CommuteAPIError(kind: .conflict, status: 409,
                                                           code: "plan_revision_conflict",
                                                           message: "计划已更新", retryable: false, requestId: nil))
        await store.updateRouteAndAnalyze()
        XCTAssertNil(store.pendingSubmission)
        XCTAssertTrue(store.errorNotice?.contains("计划已更新") == true)

        await waitUntil { self.repository.requestedKeys.count >= 2 }
        XCTAssertEqual(repository.listPlansCalls, 2, "冲突后必须重新读取计划")
        XCTAssertGreaterThanOrEqual(routeService.calls, 2, "必须按新计划重新查询路线")
        if repository.requestedBodies.count >= 2 {
            XCTAssertNotEqual(repository.requestedBodies[0], repository.requestedBodies[1],
                              "不能只改 revision 重发旧快照")
        }
    }

    func testIdempotencyKeyReusedStopsWithoutAutoRetry() async throws {
        repository.checkResult = .failure(CommuteAPIError(kind: .conflict, status: 409,
                                                           code: "idempotency_key_reused",
                                                           message: "键冲突", retryable: false, requestId: nil))
        await store.updateRouteAndAnalyze()
        await waitUntil(timeout: 1) { false }

        XCTAssertNil(store.pendingSubmission)
        XCTAssertTrue(store.errorNotice?.contains("幂等键") == true)
        XCTAssertEqual(repository.requestedKeys.count, 1, "冲突不得自动换键重试")
    }

    func testFailedCheckShowsFailureNotProgress() async throws {
        repository.checkResult = .success(
            CommuteCheck(id: "check-failed", status: .failed, advisoryId: nil,
                         errorCode: "provider_unavailable", errorMessage: "服务器未能生成建议。")
        )

        await store.updateRouteAndAnalyze()

        XCTAssertEqual(store.checkStatus, .failed)
        XCTAssertEqual(store.processingStateText, "上次分析失败")
        XCTAssertNil(store.advisory)
        XCTAssertNil(store.pendingSubmission)
    }

    func testStaleSnapshotRejectedClearsPending() async throws {
        repository.checkResult = .failure(CommuteAPIError(kind: .validation, status: 422,
                                                           code: "route_snapshot_stale",
                                                           message: "快照已过期", retryable: false, requestId: nil))
        await store.updateRouteAndAnalyze()

        XCTAssertNil(store.pendingSubmission)
        XCTAssertTrue(store.errorNotice?.contains("快照") == true)
    }

    func testRouteFailureDoesNotSubmitSnapshot() async throws {
        routeService.result = .failure(CommuteRouteError.notFound)

        await store.updateRouteAndAnalyze()

        guard case .failed = store.routeState else { return XCTFail("路线失败必须如实显示") }
        XCTAssertTrue(repository.requestedKeys.isEmpty, "没有快照就不能提交路线 AI 分析")
        XCTAssertTrue(store.errorNotice?.contains("无法提交路线快照") == true)
    }

    func testAccountSwitchDropsLateRouteResult() async throws {
        routeService.delayNanoseconds = 200_000_000

        let task = Task { await store.updateRouteAndAnalyze() }
        try? await Task.sleep(nanoseconds: 40_000_000)
        store.setAccount("user-2")
        await task.value

        XCTAssertNil(store.routeSnapshot, "切号后迟到的路线结果不得写入")
        XCTAssertEqual(store.routeState, .idle)
    }

    // MARK: - 后台运行状态（与任务进度分开）

    private func runtimeSnapshot(state: RuntimeComponentState,
                                 seenAgo: TimeInterval?,
                                 staleAfter: Int = 60,
                                 serverOffset: TimeInterval = 0,
                                 latest: RuntimeLatestCheck? = nil) -> CommuteRuntime {
        let serverNow = Date().addingTimeInterval(serverOffset)
        let seen = seenAgo.map { serverNow.addingTimeInterval(-$0) }
        let component = RuntimeComponent(state: state, lastSeenAt: seen)
        return CommuteRuntime(
            serverTime: serverNow, scheduler: component, worker: component,
            staleAfterSeconds: staleAfter,
            queue: ["queued": 1],
            latestCheck: latest,
            lastProgressAt: serverNow,
            routeProviderAvailable: true,
            autoAnalysisEnabled: false,
            autoAnalysisMode: "weather_only"
        )
    }

    private func startWaitingCheck() async {
        repository.checkResult = .success(
            CommuteCheck(id: "check-wait", status: .queued, advisoryId: nil,
                         errorCode: nil, errorMessage: nil)
        )
        await store.updateRouteAndAnalyze()
    }


    func testRuntimeAgeUsesElapsedMonotonicTimeNotWallClock() async {
        repository.runtimeResult = .success(runtimeSnapshot(state: .online, seenAgo: 5))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)
        XCTAssertEqual(store.runtimeHint?.text, "后台执行程序最近有响应。")

        let later = ProcessInfo.processInfo.systemUptime + 120
        XCTAssertEqual(store.runtimeHint(nowUptime: later)?.text,
                       "后台执行程序近期未响应，任务仍已保存。",
                       "页面停留后应按经过时长转为近期未响应")
    }

    func testPlanChangeClearsPreviousTaskAndRuntime() async {
        repository.runtimeResult = .success(runtimeSnapshot(state: .online, seenAgo: 5))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)
        XCTAssertNotNil(store.runtimeHint)

        let saved = await store.save(draft: CommutePlanDraft(plan: plan))

        XCTAssertTrue(saved)
        XCTAssertNil(store.pendingSubmission, "新计划不能继续显示旧任务的等待状态")
        XCTAssertNil(store.checkStatus)
        XCTAssertNil(store.runtime)
        XCTAssertNil(store.runtimeHint, "旧计划的后台状态不能出现在新计划")
    }

    func testRuntimeHintIsSilentBeforeFirstQuery() async {
        await startWaitingCheck()
        XCTAssertTrue(store.hasActiveCheck)
        XCTAssertNil(store.runtimeHint, "还没查过心跳时不应预先显示失败")
    }

    func testRuntimeNotSeenKeepsTaskQueued() async {
        repository.runtimeResult = .success(runtimeSnapshot(state: .notSeen, seenAgo: nil))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)

        XCTAssertEqual(store.runtimeHint?.text, "尚未收到后台执行程序的响应。")
        XCTAssertEqual(store.checkStatus, .queued, "心跳缺失不能把任务判为失败")
        XCTAssertNotNil(store.pendingSubmission)
        XCTAssertTrue(store.processingStateText?.contains("等待服务器处理") == true)
    }

    func testRuntimeStaleDoesNotBecomeFailure() async {
        repository.runtimeResult = .success(runtimeSnapshot(state: .stale, seenAgo: 3_600))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)

        XCTAssertEqual(store.runtimeHint?.text, "后台执行程序近期未响应，任务仍已保存。")
        XCTAssertEqual(store.checkStatus, .queued)
        if case .failed = store.syncState { XCTFail("心跳过期不是任务失败") }
    }

    func testRuntimeOnlineDoesNotTurnQueuedIntoAnalysis() async {
        repository.runtimeResult = .success(runtimeSnapshot(state: .online, seenAgo: 5))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)

        XCTAssertEqual(store.runtimeHint?.text, "后台执行程序最近有响应。")
        XCTAssertEqual(store.checkStatus, .queued, "心跳新鲜不代表任务在分析")
        XCTAssertTrue(store.processingStateText?.contains("等待服务器处理") == true)
    }

    func testRuntimeRequestFailureKeepsTaskAndCredentials() async {
        repository.runtimeResult = .failure(
            CommuteAPIError(kind: .offline, status: nil, code: "offline",
                            message: "offline", retryable: true, requestId: nil)
        )
        await startWaitingCheck()
        await store.refreshRuntime(force: true)

        XCTAssertEqual(store.runtimeHint?.text, "暂时无法获取后台状态。")
        XCTAssertNotNil(store.pendingSubmission)
        XCTAssertEqual(store.checkStatus, .queued)
        XCTAssertTrue(store.errorNotice?.contains("登录") != true)
    }

    func testRuntimeFailureRetainsLastQueryWithMarker() async {
        repository.runtimeResult = .success(runtimeSnapshot(state: .online, seenAgo: 5))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)
        XCTAssertNotNil(store.runtime)

        repository.runtimeResult = .failure(
            CommuteAPIError(kind: .server, status: 503, code: "unavailable",
                            message: "unavailable", retryable: true, requestId: nil)
        )
        await store.refreshRuntime(force: true)

        XCTAssertNotNil(store.runtime, "失败时保留上次结果")
        XCTAssertTrue(store.runtimeHint?.isFailure == true)
        XCTAssertTrue(store.runtimeHint?.detail?.contains("上次查询") == true)
    }

    func testRuntimeLatestCheckDoesNotOverrideCurrentCheck() async {
        let other = RuntimeLatestCheck(checkId: "another-check", status: .running,
                                       phase: .analyzing, phaseUpdatedAt: Date())
        repository.runtimeResult = .success(runtimeSnapshot(state: .online, seenAgo: 5, latest: other))
        await startWaitingCheck()
        await store.refreshRuntime(force: true)

        XCTAssertEqual(store.checkStatus, .queued, "latest_check 不能覆盖当前任务")
        XCTAssertNil(store.advisory)
        XCTAssertTrue(store.processingStateText?.contains("等待服务器处理") == true)
    }

    func testRuntimePresenceUsesServerClockAndAgesCachedOnline() {
        let skewed = runtimeSnapshot(state: .online, seenAgo: 5, serverOffset: 3_600)
        XCTAssertEqual(skewed.workerPresence(elapsedSinceFetch: 0).state, .online,
                       "设备时间与服务器不一致不应影响相对年龄")
        XCTAssertEqual(skewed.workerPresence(elapsedSinceFetch: 120).state, .stale,
                       "缓存的 online 不能一直新鲜")
        let stale = runtimeSnapshot(state: .stale, seenAgo: 3_600)
        XCTAssertEqual(stale.workerPresence(elapsedSinceFetch: 0).state, .stale)
    }

    func testAccountSwitchDropsLateRuntimeResult() async throws {
        repository.runtimeResult = .success(runtimeSnapshot(state: .online, seenAgo: 5))
        await startWaitingCheck()
        repository.runtimeDelay = 0.3

        let task = Task { await store.refreshRuntime(force: true) }
        try? await Task.sleep(nanoseconds: 40_000_000)
        store.setAccount("user-2")
        await task.value

        XCTAssertNil(store.runtime, "切号后迟到的后台状态不得写入")
        XCTAssertNil(store.runtimeHint)
    }
}
