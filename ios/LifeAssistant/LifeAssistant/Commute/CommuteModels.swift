import Foundation

// MARK: - 通用类型

/// WGS84 坐标点；label 是用户可读名称，坐标是事实数据。
struct GeoPoint: Codable, Hashable, Identifiable {
    var label: String
    var latitude: Double
    var longitude: Double

    var id: String { "\(latitude),\(longitude),\(label)" }

    enum CodingKeys: String, CodingKey {
        case label, latitude, longitude
    }
}

enum TransportMode: String, Codable, CaseIterable, Identifiable {
    case driving, walking
    var id: String { rawValue }
    var title: String { self == .driving ? "驾车" : "步行" }
    var symbol: String { self == .driving ? "car.fill" : "figure.walk" }
}

/// 周一到周日用 1...7 表示，与契约一致。
enum Weekday: Int, CaseIterable, Identifiable {
    case monday = 1, tuesday, wednesday, thursday, friday, saturday, sunday
    var id: Int { rawValue }
    var shortTitle: String {
        ["一", "二", "三", "四", "五", "六", "日"][rawValue - 1]
    }
}

enum AdvisoryQuality: String, Codable {
    case fresh, degraded, unavailable
    var title: String {
        switch self {
        case .fresh: return "数据完整"
        case .degraded: return "数据不完整"
        case .unavailable: return "暂时无法生成"
        }
    }
}

enum DepartureStatus: String, Codable {
    case upcoming
    case leaveNow = "leave_now"
    case pastDue = "past_due"
    case unknown
}

enum CheckStatus: String, Codable {
    case queued, running, succeeded, degraded, failed, cancelled
    var isTerminal: Bool {
        switch self {
        case .succeeded, .degraded, .failed, .cancelled: return true
        case .queued, .running: return false
        }
    }
    var title: String {
        switch self {
        case .queued: return "排队中"
        case .running: return "正在查询"
        case .succeeded: return "已完成"
        case .degraded: return "已完成（数据不完整）"
        case .failed: return "失败"
        case .cancelled: return "已取消"
        }
    }
}

/// Raw values are the server contract (PUT /push-devices), not UIKit status names.
enum NotificationPermission: String, Codable {
    case authorized, provisional, ephemeral, denied, unknown

    var title: String {
        switch self {
        case .authorized: return "系统已允许通知"
        case .provisional: return "系统已允许安静通知"
        case .ephemeral: return "临时授权"
        case .denied: return "系统已拒绝通知"
        case .unknown: return "尚未询问系统权限"
        }
    }
}

// MARK: - 通勤设置

struct CommutePlan: Codable, Identifiable, Hashable {
    let id: String
    var name: String
    var enabled: Bool
    var timezone: String
    var weekdays: [Int]
    var arrivalLocalTime: String
    var checkLocalTime: String
    var transportMode: TransportMode
    var bufferMinutes: Int
    var rainProbabilityThreshold: Double
    var rainExtraBufferMinutes: Int
    var notifyEnabled: Bool
    var origin: GeoPoint
    var destination: GeoPoint
    var revision: Int
    var createdAt: Date?
    var updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, enabled, timezone, weekdays, revision
        case arrivalLocalTime = "arrival_local_time"
        case checkLocalTime = "check_local_time"
        case transportMode = "transport_mode"
        case bufferMinutes = "buffer_minutes"
        case rainProbabilityThreshold = "rain_probability_threshold"
        case rainExtraBufferMinutes = "rain_extra_buffer_minutes"
        case notifyEnabled = "notify_enabled"
        case origin, destination
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

/// 创建 / 完整更新时提交的载荷；不携带 id、revision、时间戳。
struct CommutePlanDraft: Codable, Equatable {
    var name: String
    var enabled: Bool
    var timezone: String
    var weekdays: [Int]
    var arrivalLocalTime: String
    var checkLocalTime: String
    var transportMode: TransportMode
    var bufferMinutes: Int
    var rainProbabilityThreshold: Double
    var rainExtraBufferMinutes: Int
    var notifyEnabled: Bool
    var origin: GeoPoint
    var destination: GeoPoint

    enum CodingKeys: String, CodingKey {
        case name, enabled, timezone, weekdays
        case arrivalLocalTime = "arrival_local_time"
        case checkLocalTime = "check_local_time"
        case transportMode = "transport_mode"
        case bufferMinutes = "buffer_minutes"
        case rainProbabilityThreshold = "rain_probability_threshold"
        case rainExtraBufferMinutes = "rain_extra_buffer_minutes"
        case notifyEnabled = "notify_enabled"
        case origin, destination
    }

    static func empty(timezone: String = TimeZone.current.identifier) -> CommutePlanDraft {
        CommutePlanDraft(
            name: "通勤",
            enabled: true,
            timezone: timezone,
            weekdays: [1, 2, 3, 4, 5],
            arrivalLocalTime: "09:00",
            checkLocalTime: "07:30",
            transportMode: .driving,
            bufferMinutes: 10,
            rainProbabilityThreshold: 0.4,
            rainExtraBufferMinutes: 10,
            notifyEnabled: true,
            origin: GeoPoint(label: "", latitude: 0, longitude: 0),
            destination: GeoPoint(label: "", latitude: 0, longitude: 0)
        )
    }

    init(plan: CommutePlan) {
        name = plan.name
        enabled = plan.enabled
        timezone = plan.timezone
        weekdays = plan.weekdays
        arrivalLocalTime = plan.arrivalLocalTime
        checkLocalTime = plan.checkLocalTime
        transportMode = plan.transportMode
        bufferMinutes = plan.bufferMinutes
        rainProbabilityThreshold = plan.rainProbabilityThreshold
        rainExtraBufferMinutes = plan.rainExtraBufferMinutes
        notifyEnabled = plan.notifyEnabled
        origin = plan.origin
        destination = plan.destination
    }

    init(name: String, enabled: Bool, timezone: String, weekdays: [Int],
         arrivalLocalTime: String, checkLocalTime: String, transportMode: TransportMode,
         bufferMinutes: Int, rainProbabilityThreshold: Double, rainExtraBufferMinutes: Int,
         notifyEnabled: Bool, origin: GeoPoint, destination: GeoPoint) {
        self.name = name
        self.enabled = enabled
        self.timezone = timezone
        self.weekdays = weekdays
        self.arrivalLocalTime = arrivalLocalTime
        self.checkLocalTime = checkLocalTime
        self.transportMode = transportMode
        self.bufferMinutes = bufferMinutes
        self.rainProbabilityThreshold = rainProbabilityThreshold
        self.rainExtraBufferMinutes = rainExtraBufferMinutes
        self.notifyEnabled = notifyEnabled
        self.origin = origin
        self.destination = destination
    }
}

// MARK: - 检查与建议

struct CommuteCheckFailure: Codable, Equatable {
    let code: String
    let message: String
    let retryable: Bool
}

/// Matches GET /checks/{id}. The 202 response only contains check_id/status, so
/// all richer fields are optional and must not be invented client-side.
struct CommuteCheck: Codable, Identifiable {
    let id: String
    let status: CheckStatus
    let advisoryId: String?
    let error: CommuteCheckFailure?
    let planId: String?
    let trigger: String?
    let planRevision: Int?
    let advisoryCreated: Bool?
    let createdAt: Date?
    let finishedAt: Date?

    var errorCode: String? { error?.code }
    var errorMessage: String? { error?.message }

    enum CodingKeys: String, CodingKey {
        case id = "check_id"
        case status
        case advisoryId = "advisory_id"
        case error
        case planId = "plan_id"
        case trigger
        case planRevision = "plan_revision"
        case advisoryCreated = "advisory_created"
        case createdAt = "created_at"
        case finishedAt = "finished_at"
    }

    init(id: String, status: CheckStatus, advisoryId: String?,
         errorCode: String?, errorMessage: String?) {
        self.id = id
        self.status = status
        self.advisoryId = advisoryId
        self.error = errorCode.map {
            CommuteCheckFailure(code: $0, message: errorMessage ?? "检查未能完成。", retryable: false)
        }
        self.planId = nil
        self.trigger = nil
        self.planRevision = nil
        self.advisoryCreated = nil
        self.createdAt = nil
        self.finishedAt = nil
    }
}

struct WeatherAttribution: Codable, Hashable {
    let text: String?
    let url: String?
}

/// Matches the server's normalized `weather` object. The old WeatherKit-style
/// `valid_from`/`rain_probability` names are intentionally not decoded.
struct AdvisoryWeather: Codable, Hashable {
    let forecastStart: Date?
    let forecastEnd: Date?
    let temperatureC: Double?
    let precipitationProbabilityMax: Double?
    let precipitationProbabilityKnown: Bool?
    let rainRisk: Bool?
    let coverage: String?
    let rainProbabilityThreshold: Double?

    enum CodingKeys: String, CodingKey {
        case forecastStart = "forecast_start"
        case forecastEnd = "forecast_end"
        case temperatureC = "temperature_c"
        case precipitationProbabilityMax = "precipitation_probability_max"
        case precipitationProbabilityKnown = "precipitation_probability_known"
        case rainRisk = "rain_risk"
        case coverage
        case rainProbabilityThreshold = "rain_probability_threshold"
    }

    var validFrom: Date? { forecastStart }
    var validTo: Date? { forecastEnd }
    var rainProbability: Double? { precipitationProbabilityMax }
    var isMissing: Bool {
        forecastStart == nil && forecastEnd == nil && temperatureC == nil
            && precipitationProbabilityMax == nil
    }
}

struct AdvisorySource: Codable, Hashable, Identifiable {
    let kind: String?
    let provider: String
    let fetchedAt: Date?
    let validFrom: Date?
    let validTo: Date?
    let freshness: String
    let attribution: WeatherAttribution?
    let note: String?

    var id: String {
        [kind ?? "", provider,
         fetchedAt.map { String($0.timeIntervalSince1970) } ?? "",
         validFrom.map { String($0.timeIntervalSince1970) } ?? ""].joined(separator: "|")
    }

    enum CodingKeys: String, CodingKey {
        case kind, provider
        case fetchedAt = "fetched_at"
        case validFrom = "valid_from"
        case validTo = "valid_to"
        case freshness, attribution, note
    }
}

/// 服务端对路线快照的分析结论。`available` 只表示这次分析有效，
/// 不代表数据不过期；是否仍可执行由 advisory 的时段/有效期决定。
struct AiAnalysis: Codable, Equatable, Hashable {
    enum Status: String, Codable {
        case available, unavailable, rejected

        var title: String {
            switch self {
            case .available: return "智能分析可用"
            case .unavailable: return "智能分析暂不可用"
            case .rejected: return "智能分析未通过校验"
            }
        }
    }

    enum DurationSource: String, Codable {
        case durationSeconds = "duration_seconds"
        case durationInTrafficSeconds = "duration_in_traffic_seconds"
        case none

        /// 只按字段名如实说明，不推断“含实时路况”。
        var title: String {
            switch self {
            case .durationSeconds: return "路线快照时长"
            case .durationInTrafficSeconds: return "含交通时长的查询结果"
            case .none: return "没有可用耗时"
            }
        }

        var shortTitle: String {
            switch self {
            case .durationSeconds: return "快照时长"
            case .durationInTrafficSeconds: return "含交通时长"
            case .none: return "无耗时"
            }
        }
    }

    let status: Status
    let usedDurationSeconds: Int?
    let durationSource: DurationSource
    let mapDurationSeconds: Int?
    let extraBufferSeconds: Int?
    let recommendedDepartureAt: Date?
    let basis: [String]
    let missingInformation: [String]
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case status
        case usedDurationSeconds = "used_duration_seconds"
        case durationSource = "duration_source"
        case mapDurationSeconds = "map_duration_seconds"
        case extraBufferSeconds = "extra_buffer_seconds"
        case recommendedDepartureAt = "recommended_departure_at"
        case basis
        case missingInformation = "missing_information"
        case reason
    }
}

struct CommuteAdvisory: Codable, Identifiable, Hashable {
    let id: String
    let planId: String
    let planRevision: Int
    let localServiceDate: String
    let generatedAt: Date
    let expiresAt: Date
    let quality: AdvisoryQuality
    let title: String
    let summary: String
    let targetArrivalAt: Date
    let recommendedDepartureAt: Date?
    let departureStatus: DepartureStatus
    let etaSeconds: Int?
    let bufferSeconds: Int
    let weatherExtraBufferSeconds: Int
    let weather: AdvisoryWeather?
    let sources: [AdvisorySource]
    let reasonCodes: [String]
    let explanationSource: String
    var aiAnalysis: AiAnalysis? = nil

    enum CodingKeys: String, CodingKey {
        case id
        case planId = "plan_id"
        case planRevision = "plan_revision"
        case localServiceDate = "local_service_date"
        case generatedAt = "generated_at"
        case expiresAt = "expires_at"
        case quality, title, summary
        case targetArrivalAt = "target_arrival_at"
        case recommendedDepartureAt = "recommended_departure_at"
        case departureStatus = "departure_status"
        case etaSeconds = "eta_seconds"
        case bufferSeconds = "buffer_seconds"
        case weatherExtraBufferSeconds = "weather_extra_buffer_seconds"
        case weather, sources
        case reasonCodes = "reason_codes"
        case explanationSource = "explanation_source"
        case aiAnalysis = "ai_analysis"
    }

    var isExpired: Bool { expiresAt <= Date() }
}

// MARK: - 错误与响应包装

struct APIErrorBody: Codable {
    let code: String
    let message: String
    let retryable: Bool
}

struct APIErrorEnvelope: Codable {
    let error: APIErrorBody
    let requestId: String?

    enum CodingKeys: String, CodingKey {
        case error
        case requestId = "request_id"
    }
}

struct LoginResponse: Codable {
    let token: String
    let expiresAt: Date
    let userId: String

    enum CodingKeys: String, CodingKey {
        case token
        case expiresAt = "expires_at"
        case userId = "user_id"
    }
}

struct PushRegistration: Codable {
    let apnsToken: String
    let notificationPermission: String

    enum CodingKeys: String, CodingKey {
        case apnsToken = "apns_token"
        case notificationPermission = "notification_permission"
    }
}

struct AdvisoryActionRequest: Codable {
    let action: String
    let installationId: String

    enum CodingKeys: String, CodingKey {
        case action
        case installationId = "installation_id"
    }
}

// MARK: - UI 侧状态

enum CommuteSyncState: Equatable {
    case localDraft
    case syncing
    case synced(Date)
    case offline(Date)
    case failed(String, retryable: Bool)
    case needsLogin

    var isRemoteBacked: Bool {
        switch self {
        case .synced: return true
        default: return false
        }
    }

    var shortText: String {
        switch self {
        case .localDraft: return "本地草稿 · 尚未同步"
        case .syncing: return "正在同步"
        case .synced(let date): return "已同步 " + CommuteFormat.clock(date)
        case .offline(let date): return "离线缓存 " + CommuteFormat.clock(date)
        case .failed(_, let retryable): return retryable ? "同步失败，可重试" : "同步失败"
        case .needsLogin: return "未登录 · 仅本地草稿"
        }
    }
}

enum CommuteFormat {
    static let locale = Locale(identifier: "zh_CN")

    static func clock(_ date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    static func dayTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateFormat = "M月d日 HH:mm"
        return formatter.string(from: date)
    }

    static func duration(_ seconds: Int) -> String {
        let minutes = Int((Double(seconds) / 60).rounded())
        if minutes >= 60 {
            return "\(minutes / 60) 小时 \(minutes % 60) 分"
        }
        return "\(minutes) 分钟"
    }

    static func relative(_ date: Date, now: Date = Date()) -> String {
        let delta = now.timeIntervalSince(date)
        if delta < 60 { return "刚刚更新" }
        if delta < 3600 { return "\(Int(delta / 60)) 分钟前更新" }
        return dayTime(date) + " 更新"
    }
}

// MARK: - 校验（App 与服务器同规则）

enum CommuteValidation {
    static func problems(in draft: CommutePlanDraft) -> [String] {
        var issues: [String] = []
        if draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            issues.append("请填写名称。")
        }
        if draft.origin.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            issues.append("请选择出发地。")
        }
        if draft.destination.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            issues.append("请选择目的地。")
        }
        if draft.weekdays.isEmpty {
            issues.append("至少选择一个工作日。")
        }
        if draft.weekdays.contains(where: { $0 < 1 || $0 > 7 }) {
            issues.append("工作日必须是 1–7。")
        }
        if !(-90...90).contains(draft.origin.latitude) || !(-180...180).contains(draft.origin.longitude) {
            issues.append("出发地坐标超出有效范围。")
        }
        if !(-90...90).contains(draft.destination.latitude) || !(-180...180).contains(draft.destination.longitude) {
            issues.append("目的地坐标超出有效范围。")
        }
        guard let arrival = minutes(draft.arrivalLocalTime),
              let check = minutes(draft.checkLocalTime) else {
            issues.append("时间格式应为 HH:mm。")
            return issues
        }
        if check >= arrival {
            issues.append("开始检查时间必须早于到达时间；跨午夜暂不支持。")
        }
        if !(0...120).contains(draft.bufferMinutes) || !(0...120).contains(draft.rainExtraBufferMinutes) {
            issues.append("预留时间需在 0–120 分钟。")
        }
        if !(0...1).contains(draft.rainProbabilityThreshold) {
            issues.append("降雨概率阈值需在 0 到 1 之间。")
        }
        return issues
    }

    static func minutes(_ hhmm: String) -> Int? {
        let parts = hhmm.split(separator: ":")
        guard parts.count == 2,
              let hour = Int(parts[0]), let minute = Int(parts[1]),
              (0...23).contains(hour), (0...59).contains(minute) else { return nil }
        return hour * 60 + minute
    }
}

