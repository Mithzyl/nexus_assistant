import XCTest
@testable import LifeAssistant

final class StubCommuteRepository: CommuteRepository {
    var plans: [CommutePlan] = []
    var advisory: CommuteAdvisory?
    var weather: PlanWeather?
    var advisories: [String: CommuteAdvisory] = [:]
    var latestError: CommuteAPIError?
    var updateError: CommuteAPIError?
    var requestCheckError: CommuteAPIError?
    var requestCheckResult = CommuteCheck(id: "check-1", status: .queued, advisoryId: nil,
                                          errorCode: nil, errorMessage: nil)
    var checkResults: [CommuteCheck] = []
    var actions: [(String, String)] = []
    var registeredTokens: [String] = []
    var requestedChecks = 0

    func login(email: String, password: String) async throws -> LoginResponse {
        LoginResponse(token: "test-token", expiresAt: Date().addingTimeInterval(3600), userId: "user-1")
    }

    func listPlans() async throws -> [CommutePlan] { plans }

    func createPlan(_ draft: CommutePlanDraft) async throws -> CommutePlan {
        let plan = CommutePlan(
            id: "plan-new", name: draft.name, enabled: draft.enabled, timezone: draft.timezone,
            weekdays: draft.weekdays, arrivalLocalTime: draft.arrivalLocalTime,
            checkLocalTime: draft.checkLocalTime, transportMode: draft.transportMode,
            bufferMinutes: draft.bufferMinutes, rainProbabilityThreshold: draft.rainProbabilityThreshold,
            rainExtraBufferMinutes: draft.rainExtraBufferMinutes, notifyEnabled: draft.notifyEnabled,
            origin: draft.origin, destination: draft.destination,
            revision: 1, createdAt: Date(), updatedAt: Date()
        )
        plans = [plan]
        return plan
    }

    func updatePlan(id: String, draft: CommutePlanDraft, revision: Int) async throws -> CommutePlan {
        if let updateError { throw updateError }
        guard var plan = plans.first(where: { $0.id == id }) else {
            throw CommuteAPIError.missingAdvisory
        }
        plan.revision = revision + 1
        plan.name = draft.name
        plans = [plan]
        return plan
    }

    func deletePlan(id: String) async throws { plans.removeAll { $0.id == id } }

    func requestCheck(planId: String, idempotencyKey: String,
                      request: CheckCreateRequest?) async throws -> CommuteCheck {
        requestedChecks += 1
        if let requestCheckError { throw requestCheckError }
        return requestCheckResult
    }

    func check(id: String) async throws -> CommuteCheck {
        if checkResults.isEmpty { return requestCheckResult }
        return checkResults.removeFirst()
    }

    func latestAdvisory(planId: String) async throws -> CommuteAdvisory {
        if let latestError { throw latestError }
        if let advisory { return advisory }
        throw CommuteAPIError.missingAdvisory
    }

    func latestWeather(planId: String, localServiceDate: String?) async throws -> PlanWeather {
        if let weather { return weather }
        throw CommuteAPIError.missingWeather
    }

    func advisory(id: String) async throws -> CommuteAdvisory {
        if let latestError { throw latestError }
        if let found = advisories[id] { return found }
        throw CommuteAPIError.missingAdvisory
    }

    func registerDevice(installationId: String, token: String, permission: String) async throws {
        registeredTokens.append(token)
    }
    func unregisterDevice(installationId: String) async throws {}
    func reportAction(advisoryId: String, action: String, installationId: String, idempotencyKey: String) async throws {
        actions.append((advisoryId, action))
    }
}

@MainActor
final class CommuteStoreTests: XCTestCase {
    private func makeStore(_ stub: StubCommuteRepository) -> CommuteStore {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("commute-tests-" + UUID().uuidString, isDirectory: true)
        return CommuteStore(
            repository: stub,
            localStore: CommuteLocalStore(directory: directory),
            demoMode: true
        )
    }

    func testLoadAdoptsPlanAndAdvisory() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        let store = makeStore(stub)

        await store.load()

        XCTAssertEqual(store.plan?.id, DemoCommute.plan.id)
        XCTAssertEqual(store.advisory?.id, DemoCommute.advisory.id)
        if case .synced = store.syncState {} else { XCTFail("应显示已同步") }
    }

    func testMissingAdvisoryDoesNotInventOne() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.latestError = .missingAdvisory
        let store = makeStore(stub)

        await store.load()

        XCTAssertNil(store.advisory)
        XCTAssertEqual(store.plan?.id, DemoCommute.plan.id)
    }

    func testSaveConflictSurfacesConflictNotice() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        stub.updateError = CommuteAPIError(
            kind: .conflict, status: 409, code: "revision_conflict",
            message: "rev", retryable: false, requestId: "r1"
        )
        let store = makeStore(stub)
        await store.load()

        let saved = await store.save(draft: CommutePlanDraft(plan: DemoCommute.plan))

        XCTAssertFalse(saved)
        XCTAssertEqual(store.conflictNotice?.contains("其他设备"), true)
        XCTAssertEqual(store.syncState, .failed("设置已在其他设备修改，请重新加载后再保存。", retryable: false))
    }

    func testUnauthorizedMovesToNeedsLogin() async {
        XCTAssertTrue(PAKeychain.saveToken("shared-session-token"))
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.latestError = CommuteAPIError(
            kind: .unauthenticated, status: 401, code: "unauthenticated",
            message: "expired", retryable: false, requestId: nil
        )
        let store = CommuteStore(repository: stub, localStore: CommuteLocalStore(
            directory: URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("commute-token-test-" + UUID().uuidString)
        ), demoMode: false)

        await store.load()

        XCTAssertEqual(store.syncState, .needsLogin)
        XCTAssertEqual(PAKeychain.readTokenDirect(), "shared-session-token")
        PAKeychain.deleteToken()
    }

    func testRefreshOnlyReadsAndNeverCreatesCheck() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        stub.advisories[DemoCommute.advisory.id] = DemoCommute.advisory
        stub.requestCheckResult = CommuteCheck(id: "check-1", status: .queued, advisoryId: nil,
                                               errorCode: nil, errorMessage: nil)
        stub.checkResults = [
            CommuteCheck(id: "check-1", status: .running, advisoryId: nil, errorCode: nil, errorMessage: nil),
            CommuteCheck(id: "check-1", status: .succeeded, advisoryId: DemoCommute.advisory.id,
                         errorCode: nil, errorMessage: nil)
        ]
        let store = makeStore(stub)
        await store.load()

        await store.refresh()

        XCTAssertEqual(stub.requestedChecks, 0, "刷新只读取已有结果，不能创建 check")
        XCTAssertNil(store.checkStatus)
        XCTAssertEqual(store.advisory?.id, DemoCommute.advisory.id)
    }

    func testExpiredAdvisoryGoesToHistoryNotCurrent() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory(serviceDayOffset: -1)
        let store = makeStore(stub)

        await store.load()

        XCTAssertNil(store.advisory, "过期建议不能作为当前建议")
        XCTAssertEqual(store.historicalAdvisory?.id, DemoCommute.advisory.id)
    }

    func testSavingNewRevisionMovesAdviceToHistory() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        let store = makeStore(stub)
        await store.load()
        XCTAssertNotNil(store.advisory)

        let saved = await store.save(draft: CommutePlanDraft(plan: DemoCommute.plan))

        XCTAssertTrue(saved)
        XCTAssertNil(store.advisory, "旧 revision 的建议不能继续当当前建议")
        XCTAssertEqual(store.historicalAdvisory?.id, DemoCommute.advisory.id)
        XCTAssertNotNil(store.planUpdatedNotice)
        XCTAssertEqual(stub.requestedChecks, 0, "保存设置不应自动创建分析")
    }

    func testOpeningOldRevisionAdvisoryDoesNotReplaceCurrent() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        let old = CommuteAdvisory(
            id: "old-revision", planId: DemoCommute.plan.id, planRevision: 2,
            localServiceDate: DemoCommute.advisory.localServiceDate,
            generatedAt: Date().addingTimeInterval(-600), expiresAt: Date().addingTimeInterval(3600),
            quality: .degraded, title: "t", summary: "s",
            targetArrivalAt: Date().addingTimeInterval(3600), recommendedDepartureAt: nil,
            departureStatus: .unknown, etaSeconds: nil, bufferSeconds: 600,
            weatherExtraBufferSeconds: 0, weather: nil, sources: [],
            reasonCodes: [], explanationSource: "template"
        )
        stub.advisories["old-revision"] = old
        let store = makeStore(stub)
        await store.load()

        await store.loadAdvisory(id: "old-revision")

        XCTAssertEqual(store.advisory?.id, DemoCommute.advisory.id, "旧版本响应不能覆盖当前建议")
        XCTAssertEqual(store.historicalAdvisory?.id, "old-revision")
        XCTAssertEqual(store.openedAdvisory?.id, "old-revision")
    }

    func testExpiredAdvisoryIsFlagged() {
        let advisory = DemoCommute.advisory
        XCTAssertFalse(advisory.isExpired)
        let expired = CommuteAdvisory(
            id: "old", planId: advisory.planId, planRevision: 1, localServiceDate: advisory.localServiceDate,
            generatedAt: Date().addingTimeInterval(-7200), expiresAt: Date().addingTimeInterval(-3600),
            quality: .degraded, title: "t", summary: "s", targetArrivalAt: Date().addingTimeInterval(-3600),
            recommendedDepartureAt: nil, departureStatus: .pastDue, etaSeconds: nil,
            bufferSeconds: 600, weatherExtraBufferSeconds: 0, weather: nil, sources: [],
            reasonCodes: [], explanationSource: "template"
        )
        XCTAssertTrue(expired.isExpired)
    }

    func testSkipTodayReportsAction() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        let store = makeStore(stub)
        await store.load()

        await store.skipToday()

        XCTAssertEqual(stub.actions.count, 1)
        XCTAssertEqual(stub.actions.first?.0, DemoCommute.advisory.id)
        XCTAssertEqual(stub.actions.first?.1, "skip_today")
    }

    func testValidationRejectsCrossMidnight() {
        var draft = CommutePlanDraft.empty()
        draft.origin = DemoCommute.origin
        draft.destination = DemoCommute.destination
        draft.checkLocalTime = "22:00"
        draft.arrivalLocalTime = "09:00"

        let issues = CommuteValidation.problems(in: draft)

        XCTAssertTrue(issues.contains { $0.contains("跨午夜") })
    }

    func testDecodesContractPayloadWithNulls() throws {
        let json = """
        {
          "id": "adv-1",
          "plan_id": "plan-1",
          "plan_revision": 3,
          "local_service_date": "2026-10-23",
          "generated_at": "2026-10-23T00:30:00.123456Z",
          "expires_at": "2030-10-23T02:00:00Z",
          "quality": "degraded",
          "title": "暂无可靠出发时间",
          "summary": "缺少路况耗时",
          "target_arrival_at": "2026-10-23T01:00:00Z",
          "recommended_departure_at": null,
          "departure_status": "unknown",
          "eta_seconds": null,
          "buffer_seconds": 600,
          "weather_extra_buffer_seconds": 0,
          "weather": {"forecast_start": null, "forecast_end": null,
                      "temperature_c": null, "precipitation_probability_max": null,
                      "precipitation_probability_known": false, "rain_risk": null,
                      "coverage": "unavailable", "rain_probability_threshold": 0.4},
          "sources": [],
          "reason_codes": ["ROUTE_UNAVAILABLE"],
          "explanation_source": "template"
        }
        """.data(using: .utf8)!

        let advisory = try CommuteJSON.decoder.decode(CommuteAdvisory.self, from: json)

        XCTAssertEqual(advisory.quality, .degraded)
        XCTAssertEqual(advisory.departureStatus, .unknown)
        XCTAssertNil(advisory.recommendedDepartureAt)
        XCTAssertNil(advisory.etaSeconds)
        XCTAssertNil(advisory.weather?.rainProbability)
        XCTAssertEqual(advisory.planRevision, 3)
        XCTAssertFalse(advisory.isExpired)
    }

    func testServerSuitabilityFlagsAreAuthoritative() {
        var advisory = DemoCommute.advisory
        XCTAssertTrue(CommuteStore.isApplicable(advisory, plan: DemoCommute.plan))
        advisory.matchesCurrentPlan = false
        XCTAssertFalse(CommuteStore.isApplicable(advisory, plan: DemoCommute.plan))
        advisory.matchesCurrentPlan = true
        advisory.isExpiredFlag = true
        XCTAssertFalse(CommuteStore.isApplicable(advisory, plan: DemoCommute.plan))
        advisory.isExpiredFlag = false
        advisory.isCurrentServiceDate = false
        XCTAssertFalse(CommuteStore.isApplicable(advisory, plan: DemoCommute.plan))
    }

    func testLatestWeatherIsLoadedSeparatelyFromAdvisory() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        let summary = DemoCommute.advisory.weather
        stub.weather = PlanWeather(
            planId: DemoCommute.plan.id, planRevision: 2, currentPlanRevision: 3,
            localServiceDate: "2026-10-25", checkId: "check-weather", provider: "weatherkit",
            fetchedAt: Date(), validFrom: Date(), validTo: Date(),
            quality: .fresh, freshness: "fresh", coverage: "full", staleAfterSeconds: 1800,
            isStale: false, matchesCurrentPlan: false, summary: summary
        )
        let store = makeStore(stub)

        await store.load()

        XCTAssertEqual(store.latestWeather?.planId, DemoCommute.plan.id)
        XCTAssertEqual(store.latestWeather?.matchesCurrentPlan, false)
        XCTAssertNil(store.weatherNotice)
        XCTAssertEqual(store.advisory?.id, DemoCommute.advisory.id)
    }

    func testPhaseTextUsesRealStagesOnly() async {
        let stub = StubCommuteRepository()
        stub.plans = [DemoCommute.plan]
        stub.advisory = DemoCommute.advisory
        var running = CommuteCheck(id: "check-phase", status: .running, advisoryId: nil,
                                   errorCode: nil, errorMessage: nil)
        running.phase = .fetchingWeather
        stub.requestCheckResult = running
        let store = makeStore(stub)
        await store.load()

        await store.updateRouteAndAnalyze()

        XCTAssertTrue(store.processingStateText?.contains("正在更新天气") == true,
                      "应显示后端真实阶段：\(String(describing: store.processingStateText))")
    }
}
