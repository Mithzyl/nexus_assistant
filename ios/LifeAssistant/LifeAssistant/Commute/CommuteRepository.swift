import Foundation

// MARK: - 仓库协议

protocol CommuteRepository {
    func login(email: String, password: String) async throws -> LoginResponse

    func listPlans() async throws -> [CommutePlan]
    func createPlan(_ draft: CommutePlanDraft) async throws -> CommutePlan
    func updatePlan(id: String, draft: CommutePlanDraft, revision: Int) async throws -> CommutePlan
    func deletePlan(id: String) async throws

    func requestCheck(planId: String, idempotencyKey: String,
                      request: CheckCreateRequest?) async throws -> CommuteCheck
    func check(id: String) async throws -> CommuteCheck
    func latestAdvisory(planId: String) async throws -> CommuteAdvisory
    func latestWeather(planId: String, localServiceDate: String?) async throws -> PlanWeather
    func runtime() async throws -> CommuteRuntime
    func advisory(id: String) async throws -> CommuteAdvisory

    func registerDevice(installationId: String, token: String, permission: String) async throws
    func unregisterDevice(installationId: String) async throws
    func reportAction(advisoryId: String, action: String, installationId: String, idempotencyKey: String) async throws
}

// MARK: - 远端实现

struct RemoteCommuteRepository: CommuteRepository {
    var client: CommuteAPIClient

    init(client: CommuteAPIClient = CommuteAPIClient()) {
        self.client = client
    }

    func login(email: String, password: String) async throws -> LoginResponse {
        try await client.login(email: email, password: password)
    }

    func listPlans() async throws -> [CommutePlan] { try await client.listPlans() }
    func createPlan(_ draft: CommutePlanDraft) async throws -> CommutePlan { try await client.createPlan(draft) }
    func updatePlan(id: String, draft: CommutePlanDraft, revision: Int) async throws -> CommutePlan {
        try await client.updatePlan(id: id, draft: draft, revision: revision)
    }
    func deletePlan(id: String) async throws { try await client.deletePlan(id: id) }

    func requestCheck(planId: String, idempotencyKey: String,
                      request: CheckCreateRequest?) async throws -> CommuteCheck {
        try await client.requestCheck(planId: planId, idempotencyKey: idempotencyKey, request: request)
    }
    func check(id: String) async throws -> CommuteCheck { try await client.check(id: id) }
    func latestAdvisory(planId: String) async throws -> CommuteAdvisory {
        do {
            return try await client.latestAdvisory(planId: planId)
        } catch let error as CommuteAPIError where error.status == 404 {
            throw CommuteAPIError.missingAdvisory
        }
    }
    func latestWeather(planId: String, localServiceDate: String?) async throws -> PlanWeather {
        try await client.latestWeather(planId: planId, localServiceDate: localServiceDate)
    }
    func runtime() async throws -> CommuteRuntime { try await client.runtime() }
    func advisory(id: String) async throws -> CommuteAdvisory { try await client.advisory(id: id) }

    func registerDevice(installationId: String, token: String, permission: String) async throws {
        try await client.registerDevice(installationId: installationId, token: token, permission: permission)
    }
    func unregisterDevice(installationId: String) async throws {
        try await client.unregisterDevice(installationId: installationId)
    }
    func reportAction(advisoryId: String, action: String, installationId: String, idempotencyKey: String) async throws {
        try await client.reportAction(advisoryId: advisoryId, action: action,
                                      installationId: installationId, idempotencyKey: idempotencyKey)
    }
}

enum CommuteRepositoryFactory {
    /// 仅 DEBUG + 显式环境变量时使用演示仓库；正式构建绝不会自动回退到假数据。
    static func make() -> CommuteRepository {
        #if DEBUG
        if ProcessInfo.processInfo.environment["COMMUTE_DEMO"] == "1" {
            return MockCommuteRepository.demo()
        }
        #endif
        return RemoteCommuteRepository()
    }

    static var isDemoBuild: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.environment["COMMUTE_DEMO"] == "1"
        #else
        return false
        #endif
    }
}

// MARK: - 本地存储

/// 只存草稿、最近一次成功拉取的计划和建议，用于离线展示；不伪造“已同步”。
struct CommuteLocalStore {
    struct Snapshot: Codable {
        var plans: [CommutePlan]
        var advisory: CommuteAdvisory?
        /// 不属于当前计划版本或已过期的建议：只用于历史展示，不能当当前建议。
        var historicalAdvisory: CommuteAdvisory?
        var fetchedAt: Date
        var accountId: String?
    }

    private let directory: URL
    private let fileManager = FileManager.default

    init(directory: URL? = nil) {
        if let directory {
            self.directory = directory
        } else {
            let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? fileManager.temporaryDirectory
            self.directory = base.appendingPathComponent("Commute", isDirectory: true)
        }
        try? fileManager.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }

    private var snapshotURL: URL { directory.appendingPathComponent("snapshot.json") }
    private var draftURL: URL { directory.appendingPathComponent("draft.json") }
    private var pendingURL: URL { directory.appendingPathComponent("pending-check.json") }

    /// 按账号保存“结果未知的提交”，重启后仍可原样核对，不会重复生成分析。
    func loadPending(account: String) -> PendingSubmission? {
        guard let data = try? Data(contentsOf: pendingURL),
              let pending = try? CommuteJSON.decoder.decode(PendingSubmission.self, from: data),
              pending.accountId == account else { return nil }
        return pending
    }

    func savePending(_ pending: PendingSubmission?) {
        guard let pending else {
            try? fileManager.removeItem(at: pendingURL)
            return
        }
        guard let data = try? CommuteJSON.encoder.encode(pending) else { return }
        try? data.write(to: pendingURL, options: .atomic)
    }

    func loadSnapshot(account: String) -> Snapshot? {
        guard let data = try? Data(contentsOf: snapshotURL),
              let snapshot = try? CommuteJSON.decoder.decode(Snapshot.self, from: data),
              snapshot.accountId == account else { return nil }
        return snapshot
    }

    func saveSnapshot(_ snapshot: Snapshot) {
        guard let data = try? CommuteJSON.encoder.encode(snapshot) else { return }
        try? data.write(to: snapshotURL, options: .atomic)
    }

    func loadDraft() -> CommutePlanDraft? {
        guard let data = try? Data(contentsOf: draftURL) else { return nil }
        return try? CommuteJSON.decoder.decode(CommutePlanDraft.self, from: data)
    }

    func saveDraft(_ draft: CommutePlanDraft) {
        guard let data = try? CommuteJSON.encoder.encode(draft) else { return }
        try? data.write(to: draftURL, options: .atomic)
    }

    func clear() {
        try? fileManager.removeItem(at: snapshotURL)
        try? fileManager.removeItem(at: draftURL)
    }
}

// MARK: - 演示 / 测试仓库

/// 只用于预览、测试和 DEBUG 演示；不会被正式构建自动选中。
actor MockCommuteRepository: CommuteRepository {
    private var plans: [CommutePlan]
    private var advisories: [String: CommuteAdvisory]
    private var checks: [String: CommuteCheck] = [:]
    private var checkStart: [String: Date] = [:]

    init(plans: [CommutePlan], advisories: [CommuteAdvisory]) {
        self.plans = plans
        self.advisories = Dictionary(uniqueKeysWithValues: advisories.map { ($0.id, $0) })
    }

    static func demo() -> MockCommuteRepository {
        #if DEBUG
        if ProcessInfo.processInfo.environment["COMMUTE_DEMO_PLAN"] == "none" {
            return MockCommuteRepository(plans: [], advisories: [])
        }
        #endif
        return MockCommuteRepository(plans: [DemoCommute.plan], advisories: [DemoCommute.advisory])
    }

    func login(email: String, password: String) async throws -> LoginResponse {
        LoginResponse(token: "demo-token", expiresAt: Date().addingTimeInterval(3600), userId: "demo-user")
    }

    func listPlans() async throws -> [CommutePlan] { plans }

    func createPlan(_ draft: CommutePlanDraft) async throws -> CommutePlan {
        let plan = CommutePlan(
            id: UUID().uuidString.lowercased(), name: draft.name, enabled: draft.enabled,
            timezone: draft.timezone, weekdays: draft.weekdays,
            arrivalLocalTime: draft.arrivalLocalTime, checkLocalTime: draft.checkLocalTime,
            transportMode: draft.transportMode, bufferMinutes: draft.bufferMinutes,
            rainProbabilityThreshold: draft.rainProbabilityThreshold,
            rainExtraBufferMinutes: draft.rainExtraBufferMinutes,
            notifyEnabled: draft.notifyEnabled, origin: draft.origin, destination: draft.destination,
            revision: 1, createdAt: Date(), updatedAt: Date()
        )
        plans.append(plan)
        return plan
    }

    func updatePlan(id: String, draft: CommutePlanDraft, revision: Int) async throws -> CommutePlan {
        guard let index = plans.firstIndex(where: { $0.id == id }) else {
            throw CommuteAPIError(kind: .notFound, status: 404, code: "not_found",
                                  message: "没有这条通勤设置。", retryable: false, requestId: nil)
        }
        guard plans[index].revision == revision else {
            throw CommuteAPIError(kind: .conflict, status: 409, code: "revision_conflict",
                                  message: "设置已在其他设备修改。", retryable: false, requestId: nil)
        }
        let updated = CommutePlan(
            id: id, name: draft.name, enabled: draft.enabled, timezone: draft.timezone,
            weekdays: draft.weekdays, arrivalLocalTime: draft.arrivalLocalTime,
            checkLocalTime: draft.checkLocalTime, transportMode: draft.transportMode,
            bufferMinutes: draft.bufferMinutes, rainProbabilityThreshold: draft.rainProbabilityThreshold,
            rainExtraBufferMinutes: draft.rainExtraBufferMinutes, notifyEnabled: draft.notifyEnabled,
            origin: draft.origin, destination: draft.destination,
            revision: revision + 1, createdAt: plans[index].createdAt, updatedAt: Date()
        )
        plans[index] = updated
        return updated
    }

    func deletePlan(id: String) async throws {
        plans.removeAll { $0.id == id }
    }

    /// 仅演示构建可用：固定“排队中/失败”场景，用于界面状态验证，不代表真实后端能力。
    private var demoCheckScenario: String? {
        #if DEBUG
        return ProcessInfo.processInfo.environment["COMMUTE_DEMO_CHECK"]
        #else
        return nil
        #endif
    }

    func requestCheck(planId: String, idempotencyKey: String,
                      request: CheckCreateRequest?) async throws -> CommuteCheck {
        if let scenario = demoCheckScenario {
            let failed = scenario == "failed"
            let check = CommuteCheck(id: "demo-check", status: failed ? .failed : .queued,
                                     advisoryId: nil,
                                     errorCode: failed ? "demo_unavailable" : nil,
                                     errorMessage: failed ? "演示：服务器暂时无法生成建议。" : nil)
            checks[check.id] = check
            checkStart[check.id] = Date()
            return check
        }
        let id = "check-" + UUID().uuidString.prefix(8)
        let check = CommuteCheck(id: String(id), status: .queued, advisoryId: nil,
                                 errorCode: nil, errorMessage: nil)
        checks[String(id)] = check
        checkStart[String(id)] = Date()
        return check
    }

    func check(id: String) async throws -> CommuteCheck {
        if demoCheckScenario == "queued", let queued = checks[id] {
            return queued
        }
        guard let current = checks[id] else {
            throw CommuteAPIError(kind: .notFound, status: 404, code: "not_found",
                                  message: "没有这个检查任务。", retryable: false, requestId: nil)
        }
        guard current.status == .queued,
              let started = checkStart[id],
              Date().timeIntervalSince(started) > 1.2 else {
            return current
        }
        // 新计划版本要生成匹配的分析结果；否则客户端会把它当历史结果而看不到新建议。
        var advisoryId: String?
        if let plan = plans.first {
            let generated = DemoCommute.advisory(for: plan)
            advisories[generated.id] = generated
            advisoryId = generated.id
        }
        let done = CommuteCheck(id: id, status: .succeeded, advisoryId: advisoryId,
                                errorCode: nil, errorMessage: nil)
        checks[id] = done
        return done
    }

    func latestAdvisory(planId: String) async throws -> CommuteAdvisory {
        guard let plan = plans.first(where: { $0.id == planId }),
              let advisory = advisories.values.first(where: { $0.planId == plan.id }) else {
            throw CommuteAPIError.missingAdvisory
        }
        return advisory
    }

    func latestWeather(planId: String, localServiceDate: String?) async throws -> PlanWeather {
        guard let plan = plans.first(where: { $0.id == planId }),
              let advisory = advisories.values.first(where: { $0.planId == plan.id }) else {
            throw CommuteAPIError.missingWeather
        }
        let weatherSource = advisory.sources.first { $0.kind == "weather" }
        let stale = advisory.expiresAt <= Date()
        return PlanWeather(
            planId: plan.id, planRevision: advisory.planRevision,
            currentPlanRevision: plan.revision,
            localServiceDate: localServiceDate ?? advisory.localServiceDate,
            checkId: "demo-check",
            provider: weatherSource?.provider ?? "weatherkit",
            fetchedAt: weatherSource?.fetchedAt ?? advisory.generatedAt,
            validFrom: advisory.weather?.forecastStart,
            validTo: advisory.weather?.forecastEnd,
            quality: advisory.quality,
            freshness: stale ? "stale" : "fresh",
            coverage: advisory.weather?.coverage ?? "full",
            staleAfterSeconds: 1800,
            isStale: stale,
            matchesCurrentPlan: advisory.planRevision == plan.revision,
            summary: advisory.weather
        )
    }

    func runtime() async throws -> CommuteRuntime {
        #if DEBUG
        let env = ProcessInfo.processInfo.environment
        let scenario = env["COMMUTE_DEMO_RUNTIME"] ?? "not_seen"
        if scenario == "error" {
            throw CommuteAPIError(kind: .server, status: 503, code: "runtime_unavailable",
                                  message: "暂时无法获取后台状态。", retryable: true, requestId: nil)
        }
        let offset = Double(env["COMMUTE_DEMO_SERVER_OFFSET"] ?? "") ?? 0
        let serverNow = Date().addingTimeInterval(offset)
        let state: RuntimeComponentState = {
            switch scenario {
            case "online": return .online
            case "stale": return .stale
            default: return .notSeen
            }
        }()
        let seenAgo: TimeInterval? = {
            if state == .notSeen { return nil }
            if let raw = env["COMMUTE_DEMO_SEEN_AGO"], let seconds = Double(raw) { return seconds }
            return state == .stale ? 3_600 : 5
        }()
        let component = RuntimeComponent(
            state: state,
            lastSeenAt: seenAgo.map { serverNow.addingTimeInterval(-$0) }
        )
        return CommuteRuntime(
            serverTime: serverNow,
            scheduler: component,
            worker: component,
            staleAfterSeconds: 60,
            queue: ["queued": state == .online ? 1 : 0, "running": 0],
            // 故意给一条不属于当前页面的 latest_check，用于确认客户端不会拿它覆盖当前任务。
            latestCheck: RuntimeLatestCheck(checkId: "demo-other-check", status: .running,
                                            phase: .analyzing, phaseUpdatedAt: serverNow),
            lastProgressAt: serverNow,
            routeProviderAvailable: true,
            autoAnalysisEnabled: false,
            autoAnalysisMode: "weather_only"
        )
        #else
        throw CommuteAPIError(kind: .server, status: nil, code: "demo_only",
                              message: "演示仓库仅在 DEBUG 可用。", retryable: false, requestId: nil)
        #endif
    }

    func advisory(id: String) async throws -> CommuteAdvisory {
        guard let advisory = advisories[id] else { throw CommuteAPIError.missingAdvisory }
        return advisory
    }

    func registerDevice(installationId: String, token: String, permission: String) async throws {}
    func unregisterDevice(installationId: String) async throws {}
    func reportAction(advisoryId: String, action: String, installationId: String, idempotencyKey: String) async throws {}

    private func advisoryForLatestPlan() -> CommuteAdvisory? {
        advisories.values.first
    }
}

// MARK: - 演示数据（仅 Mock）

enum DemoCommute {
    static let origin = GeoPoint(label: "家 · 柳南小区", latitude: 24.3135, longitude: 109.4132)
    static let destination = GeoPoint(label: "公司 · 城中区", latitude: 24.3265, longitude: 109.4287)

    static let plan = CommutePlan(
        id: "demo-plan", name: "上班通勤", enabled: true,
        timezone: "Asia/Shanghai", weekdays: [1, 2, 3, 4, 5],
        arrivalLocalTime: "09:00", checkLocalTime: "07:30",
        transportMode: .driving, bufferMinutes: 10,
        rainProbabilityThreshold: 0.4, rainExtraBufferMinutes: 10,
        notifyEnabled: true, origin: origin, destination: destination,
        revision: 3, createdAt: Date().addingTimeInterval(-86_400 * 5),
        updatedAt: Date().addingTimeInterval(-3600)
    )

    static var advisory: CommuteAdvisory {
        advisory(serviceDayOffset: demoServiceDayOffset)
    }

    static func advisory(serviceDayOffset: Int, now: Date = Date()) -> CommuteAdvisory {
        advisory(for: plan, serviceDayOffset: serviceDayOffset, now: now)
    }

    /// 按给定计划生成演示建议：修改到达时间后，新分析会得到匹配新计划的结果。
    static func advisory(for plan: CommutePlan, serviceDayOffset: Int? = nil,
                         now: Date = Date()) -> CommuteAdvisory {
        let timezone = TimeZone(identifier: plan.timezone) ?? .current
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timezone
        let offset = serviceDayOffset ?? demoServiceDayOffset
        let base = calendar.date(byAdding: .day, value: offset, to: now) ?? now
        let parts = plan.arrivalLocalTime.split(separator: ":").compactMap { Int($0) }
        let hour = parts.count > 0 ? parts[0] : 9
        let minute = parts.count > 1 ? parts[1] : 0
        let arrival = calendar.date(bySettingHour: hour, minute: minute, second: 0, of: base) ?? base
        let eta = 26 * 60
        let baseBuffer = plan.bufferMinutes * 60
        let rainExtra = plan.rainExtraBufferMinutes * 60
        let departure = arrival.addingTimeInterval(-Double(eta + baseBuffer + rainExtra))
        let windowStart = departure.addingTimeInterval(-30 * 60)
        let windowEnd = arrival.addingTimeInterval(15 * 60)
        let generated = offset == 0
            ? departure.addingTimeInterval(-5 * 60)
            : now.addingTimeInterval(-180)

        let serviceFormatter = DateFormatter()
        serviceFormatter.locale = Locale(identifier: "en_US_POSIX")
        serviceFormatter.timeZone = timezone
        serviceFormatter.dateFormat = "yyyy-MM-dd"

        return CommuteAdvisory(
            id: "demo-advisory-\(plan.revision)",
            planId: plan.id, planRevision: plan.revision,
            localServiceDate: serviceFormatter.string(from: arrival),
            generatedAt: generated, expiresAt: arrival.addingTimeInterval(1800),
            quality: .fresh,
            title: "\(CommuteFormat.clock(departure, timeZone: timezone)) 出发",
            summary: "\(CommuteFormat.clock(departure, timeZone: timezone)) 出发，\(CommuteFormat.clock(arrival, timeZone: timezone)) 前到达；可能有雨，已多留 \(plan.bufferMinutes + plan.rainExtraBufferMinutes) 分钟。",
            targetArrivalAt: arrival,
            recommendedDepartureAt: departure,
            departureStatus: .upcoming,
            etaSeconds: eta, bufferSeconds: baseBuffer, weatherExtraBufferSeconds: rainExtra,
            weather: AdvisoryWeather(
                forecastStart: windowStart, forecastEnd: windowEnd,
                temperatureC: 21.5, precipitationProbabilityMax: 0.55,
                precipitationProbabilityKnown: true, rainRisk: true, coverage: "full",
                rainProbabilityThreshold: plan.rainProbabilityThreshold
            ),
            sources: [
                AdvisorySource(kind: "weather", provider: "weatherkit",
                               fetchedAt: generated,
                               validFrom: windowStart, validTo: windowEnd,
                               freshness: "fresh",
                               attribution: WeatherAttribution(
                                   text: "天气数据由 Apple Weather 提供",
                                   url: "https://developer.apple.com/weatherkit/data-source-attribution/"),
                               note: "按出发地/目的地相关预报时段估算"),
                AdvisorySource(kind: "route", provider: "apple_maps_server_api",
                               fetchedAt: generated,
                               validFrom: departure, validTo: arrival,
                               freshness: "fresh", attribution: nil, note: nil)
            ],
            reasonCodes: ["WEATHER_RAIN_THRESHOLD_MET", "ROUTE_FRESH"],
            explanationSource: "template"
        )
    }

    static var demoServiceDayOffset: Int {
        #if DEBUG
        return ProcessInfo.processInfo.environment["COMMUTE_DEMO_DAY"] == "today" ? 0 : 1
        #else
        return 1
        #endif
    }
}
