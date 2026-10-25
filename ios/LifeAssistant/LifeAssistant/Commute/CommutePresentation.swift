import Foundation

// MARK: - 日期与时区

enum CommuteDay {
    static func parse(_ localServiceDate: String, timezone: TimeZone) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timezone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: localServiceDate)
    }

    static func offset(from serviceDate: Date, timezone: TimeZone, now: Date) -> Int {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timezone
        let start = calendar.startOfDay(for: serviceDate)
        let today = calendar.startOfDay(for: now)
        return calendar.dateComponents([.day], from: today, to: start).day ?? 0
    }

    static func label(offset: Int, serviceDate: Date, timezone: TimeZone) -> String {
        switch offset {
        case 0: return "今天"
        case 1: return "明天"
        case -1: return "昨天"
        default:
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "zh_CN")
            formatter.timeZone = timezone
            formatter.dateFormat = "M月d日"
            return formatter.string(from: serviceDate)
        }
    }
}

// MARK: - 结构化展示

/// 首页、详情、标题共用的一份展示模型。
/// 所有时间只来自结构化字段；服务端 title/summary 不参与主要时间表达。
struct CommutePresentation: Equatable {
    enum Phase: Equatable {
        case draft        // 没有已保存计划，或只有本地草稿
        case noAdvisory   // 有计划，但还没有任何建议
        case actionable   // 这次通勤仍有执行意义
        case dayPassed    // 本次服务时段已过
        case expired      // 建议本身已过期
    }

    enum WeatherState: Equatable {
        case loading
        case fresh
        case refreshing
        case stale
        case unavailable
    }

    struct WeatherSource: Equatable {
        let text: String
        let url: URL?
    }

    let phase: Phase
    let planName: String
    let dayLabel: String
    let headline: String
    let arrivalLine: String
    let weatherLine: String
    let statusText: String
    let updatedText: String?
    var weatherState: WeatherState = .unavailable
    var weatherNotice: String?
    var weatherUpdatedText: String?
    var weatherTemperatureText: String?
    var weatherRainText: String?
    var weatherForecastText: String?
    var weatherLocation: String?
    var weatherSource: WeatherSource?
    var aiStatusText: String?
    var aiDurationSourceText: String?
    var aiMapDurationText: String?
    var aiExtraBufferText: String?
    var aiBasis: [String] = []
    var aiMissing: [String] = []
    var aiIsHistorical = false
    var aiTodayLine: String?

    var canNavigate: Bool { phase == .actionable }
    var primaryActionTitle: String {
        switch phase {
        case .draft: return "完成设置"
        case .actionable: return "开始导航"
        case .dayPassed: return "查看安排"
        case .expired: return "查看安排"
        case .noAdvisory: return "去设置"
        }
    }

    static func make(
        advisory: CommuteAdvisory?,
        plan: CommutePlan?,
        syncState: CommuteSyncState,
        demoMode: Bool,
        isRefreshing: Bool = false,
        historicalOverride: Bool = false,
        now: Date = Date()
    ) -> CommutePresentation {
        guard let plan else {
            return CommutePresentation(
                phase: .draft,
                planName: "通勤",
                dayLabel: "",
                headline: "设置通勤提醒",
                arrivalLine: "固定出发地和目的地，由服务器检查天气与路况",
                weatherLine: "",
                statusText: "未设置",
                updatedText: nil
            )
        }

        let timezone = TimeZone(identifier: plan.timezone) ?? .current
        guard let advisory else {
            return CommutePresentation(
                phase: .noAdvisory,
                planName: plan.name,
                dayLabel: "",
                headline: "当前没有适用的建议",
                arrivalLine: "\(plan.arrivalLocalTime) 前到达 · \(plan.transportMode.title)",
                weatherLine: "",
                statusText: syncText(syncState, demoMode: demoMode),
                updatedText: nil
            )
        }

        let serviceDate = CommuteDay.parse(advisory.localServiceDate, timezone: timezone) ?? advisory.targetArrivalAt
        let offset = CommuteDay.offset(from: serviceDate, timezone: timezone, now: now)
        let dayLabel = CommuteDay.label(offset: offset, serviceDate: serviceDate, timezone: timezone)

        let departure = advisory.recommendedDepartureAt
        let arrivalText = CommuteFormat.clock(advisory.targetArrivalAt, timeZone: timezone)
        let durationText = advisory.etaSeconds.map { CommuteFormat.duration($0) }

        let phase = resolvePhase(advisory: advisory, offset: offset, now: now)

        var headline = "暂无可靠出发时间"
        if let departure {
            headline = "\(CommuteFormat.clock(departure, timeZone: timezone)) 建议出发"
        }
        if phase == .dayPassed && offset == 0 {
            headline = "今日通勤时段已过"
        }

        let arrivalLine: String
        if let durationText {
            arrivalLine = "\(arrivalText) 前到 · 路程约 \(durationText)"
        } else {
            arrivalLine = "\(arrivalText) 前到 · 缺少路程耗时"
        }

        let weather = advisory.weather
        let covered = weatherCoversCommute(advisory, timezone: timezone)
        let weatherState = weatherState(syncState: syncState, hasWeather: covered, isRefreshing: isRefreshing)
        let weatherSource = advisory.sources.first { ($0.kind ?? "") == "weather" }
        let weatherUpdated: String? = weatherSource?.fetchedAt.map {
            "天气更新于 \(CommuteFormat.clock($0, timeZone: timezone))"
        }
        let sourceInfo = weatherSource?.attribution.flatMap { attribution -> WeatherSource? in
            guard let urlString = attribution.url, let url = URL(string: urlString) else { return nil }
            return WeatherSource(text: attribution.text ?? "天气数据来源", url: url)
        }
        let rainKnown = weather?.precipitationProbabilityKnown != false && weather?.rainProbability != nil
        let forecastText: String? = {
            guard let from = weather?.validFrom, let to = weather?.validTo else { return nil }
            return "预报时段 \(CommuteFormat.clock(from, timeZone: timezone))–\(CommuteFormat.clock(to, timeZone: timezone))"
        }()

        let ai = advisory.aiAnalysis
        let aiHistorical = phase != .actionable || historicalOverride
        let aiMapText = ai?.mapDurationSeconds.map { "地图耗时 \(CommuteFormat.duration($0))" }
        let aiExtraText = ai?.extraBufferSeconds.map { "额外预留 \(CommuteFormat.duration($0))" }
        let aiTodayLine: String? = {
            guard let ai else { return nil }
            if aiHistorical {
                var parts = ["历史 AI 结果", ai.durationSource.shortTitle]
                if let extra = ai.extraBufferSeconds {
                    parts.append("预留 \(CommuteFormat.duration(extra))")
                }
                if !ai.missingInformation.isEmpty {
                    parts.append("缺 \(ai.missingInformation.count) 项")
                }
                parts.append("已过期，不代表当前出行")
                return parts.joined(separator: " · ")
            }
            switch ai.status {
            case .available:
                let missing = ai.missingInformation.isEmpty ? "" : " · 缺 \(ai.missingInformation.count) 项"
                let buffer = ai.extraBufferSeconds.map { " · 预留 \(CommuteFormat.duration($0))" } ?? ""
                return "AI 可用 · \(ai.durationSource.shortTitle)\(buffer)\(missing)"
            case .unavailable:
                return "AI 暂不可用，仅显示路线与天气"
            case .rejected:
                return "AI 分析未通过校验"
            }
        }()

        return CommutePresentation(
            phase: phase,
            planName: plan.name,
            dayLabel: dayLabel,
            headline: headline,
            arrivalLine: arrivalLine,
            weatherLine: weatherLine(advisory, plan: plan),
            statusText: statusText(phase: phase, syncState: syncState, demoMode: demoMode, advisory: advisory),
            updatedText: CommuteFormat.relative(advisory.generatedAt, now: now),
            weatherState: weatherState,
            weatherNotice: weatherNotice(weatherState),
            weatherUpdatedText: weatherUpdated,
            weatherTemperatureText: covered ? weather?.temperatureC.map { String(format: "%.0f°", $0) } : nil,
            weatherRainText: covered && rainKnown ? weather?.rainProbability.map { "\(Int(($0 * 100).rounded()))%" } : nil,
            weatherForecastText: covered ? forecastText : nil,
            weatherLocation: plan.destination.label.isEmpty ? nil : plan.destination.label,
            weatherSource: sourceInfo,
            aiStatusText: ai?.status.title,
            aiDurationSourceText: ai.map { "耗时来源：\($0.durationSource.title)" },
            // 保留字段顺序：其余参数在下方继续传递
            aiMapDurationText: aiMapText,
            aiExtraBufferText: aiExtraText,
            aiBasis: ai?.basis ?? [],
            aiMissing: ai?.missingInformation ?? [],
            aiIsHistorical: aiHistorical,
            aiTodayLine: aiTodayLine
        )
    }

    static func weatherState(syncState: CommuteSyncState, hasWeather: Bool, isRefreshing: Bool) -> WeatherState {
        guard hasWeather else { return .unavailable }
        if isRefreshing { return .refreshing }
        switch syncState {
        case .offline, .failed: return .stale
        default: return .fresh
        }
    }

    static func weatherNotice(_ state: WeatherState) -> String? {
        switch state {
        case .refreshing: return "正在更新天气…"
        case .stale: return "更新失败，当前显示上次数据"
        default: return nil
        }
    }

    // MARK: - 判定

    static func resolvePhase(advisory: CommuteAdvisory, offset: Int, now: Date) -> Phase {
        if offset < 0 { return .expired }
        if offset == 0 && now >= advisory.targetArrivalAt { return .dayPassed }
        if advisory.expiresAt <= now { return .expired }
        return .actionable
    }

    /// 天气窗口必须覆盖本次通勤（出发到到达），否则不能拿来解释出行。
    static func weatherCoversCommute(_ advisory: CommuteAdvisory, timezone: TimeZone) -> Bool {
        guard let weather = advisory.weather,
              let validFrom = weather.validFrom,
              let validTo = weather.validTo else { return false }
        let start = advisory.recommendedDepartureAt ?? advisory.targetArrivalAt.addingTimeInterval(-900)
        return validFrom <= start && validTo >= advisory.targetArrivalAt
    }

    /// 通勤时段天气文案；窗口不匹配时明确说暂不可用，而不是套用别的时间段。
    static func weatherLine(_ advisory: CommuteAdvisory, plan: CommutePlan) -> String {
        let timezone = TimeZone(identifier: plan.timezone) ?? .current
        guard advisory.weather != nil else { return "天气暂不可用" }
        guard weatherCoversCommute(advisory, timezone: timezone),
              let weather = advisory.weather else {
            return "天气暂不可用"
        }
        if weather.precipitationProbabilityKnown == false {
            return "行程时段降雨概率暂无数据"
        }
        guard let rain = weather.rainProbability else { return "行程时段天气数据不完整" }
        let threshold = plan.rainProbabilityThreshold
        if rain >= threshold {
            let extra = advisory.weatherExtraBufferSeconds / 60
            return extra > 0
                ? "可能有雨 \(Int(rain * 100))%，额外预留 \(extra) 分钟"
                : "可能有雨 \(Int(rain * 100))%"
        }
        return "降雨概率 \(Int(rain * 100))%"
    }

    // MARK: - 状态文案

    private static func syncText(_ state: CommuteSyncState, demoMode: Bool) -> String {
        if case .localDraft = state { return "本地草稿" }
        if case .needsLogin = state { return "仅本地草稿" }
        if case .synced = state { return demoMode ? "示例数据" : "已同步" }
        if case .offline = state { return "离线缓存" }
        if case .failed = state { return "未同步" }
        return "未同步"
    }

    private static func statusText(
        phase: Phase,
        syncState: CommuteSyncState,
        demoMode: Bool,
        advisory: CommuteAdvisory
    ) -> String {
        switch phase {
        case .dayPassed: return "时段已过"
        case .expired: return "建议已过期"
        default:
            return syncText(syncState, demoMode: demoMode)
        }
    }
}
