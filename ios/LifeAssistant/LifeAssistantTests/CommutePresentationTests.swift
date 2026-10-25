import XCTest
@testable import LifeAssistant

@MainActor
final class CommutePresentationTests: XCTestCase {
    private let timezone = TimeZone(identifier: "Asia/Shanghai")!

    private func date(_ y: Int, _ m: Int, _ d: Int, _ h: Int, _ min: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timezone
        return calendar.date(from: DateComponents(year: y, month: m, day: d, hour: h, minute: min))!
    }

    private func plan() -> CommutePlan {
        var plan = DemoCommute.plan
        plan.timezone = "Asia/Shanghai"
        return plan
    }

    private func advisory(
        serviceDate: Date,
        arrival: Date,
        departure: Date,
        expires: Date,
        title: String = "8:26 出发",
        summary: String = "预计 8:26 出发",
        weatherStart: Date? = nil,
        weatherEnd: Date? = nil,
        rain: Double? = 0.55,
        rainKnown: Bool = true,
        eta: Int? = 26 * 60
    ) -> CommuteAdvisory {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timezone
        formatter.dateFormat = "yyyy-MM-dd"
        return CommuteAdvisory(
            id: "adv", planId: plan().id, planRevision: 1,
            localServiceDate: formatter.string(from: serviceDate),
            generatedAt: departure.addingTimeInterval(-300),
            expiresAt: expires,
            quality: .fresh,
            title: title,
            summary: summary,
            targetArrivalAt: arrival,
            recommendedDepartureAt: departure,
            departureStatus: .upcoming,
            etaSeconds: eta,
            bufferSeconds: 10 * 60,
            weatherExtraBufferSeconds: 10 * 60,
            weather: AdvisoryWeather(
                forecastStart: weatherStart, forecastEnd: weatherEnd,
                temperatureC: 20, precipitationProbabilityMax: rain,
                precipitationProbabilityKnown: rainKnown, rainRisk: true, coverage: "full",
                rainProbabilityThreshold: 0.4
            ),
            sources: [],
            reasonCodes: [],
            explanationSource: "template"
        )
    }

    func testHeadlineUsesStructuredDepartureNotServerText() {
        let now = date(2026, 10, 23, 8, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800)),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertEqual(model.headline, "08:14 建议出发")
        XCTAssertTrue(model.arrivalLine.contains("09:00 前到"))
        XCTAssertFalse(model.headline.contains("8:26"))
    }

    func testPassedServiceDayIsNotPresentedAsUpcoming() {
        let now = date(2026, 10, 23, 15, 41)
        let arrival = date(2026, 10, 23, 9, 0)
        let departure = date(2026, 10, 23, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800)),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertEqual(model.phase, .dayPassed)
        XCTAssertEqual(model.headline, "今日通勤时段已过")
        XCTAssertFalse(model.canNavigate)
        XCTAssertEqual(model.primaryActionTitle, "查看安排")
        XCTAssertEqual(model.dayLabel, "今天")
    }

    func testTomorrowIsLabelledExplicitly() {
        let now = date(2026, 10, 23, 15, 41)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800)),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertEqual(model.dayLabel, "明天")
        XCTAssertEqual(model.phase, .actionable)
        XCTAssertTrue(model.canNavigate)
    }

    func testWeatherOutsideCommuteWindowIsUnavailable() {
        let now = date(2026, 10, 23, 8, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let afternoon = date(2026, 10, 23, 13, 56)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800),
                               weatherStart: afternoon, weatherEnd: afternoon.addingTimeInterval(3600)),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertEqual(model.weatherLine, "天气暂不可用")
        XCTAssertNil(model.weatherTemperatureText)
        XCTAssertNil(model.weatherRainText)
        XCTAssertEqual(model.weatherState, .unavailable)
    }

    func testWeatherCoveringCommuteIsUsed() {
        let now = date(2026, 10, 23, 8, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800),
                               weatherStart: departure.addingTimeInterval(-1800),
                               weatherEnd: arrival.addingTimeInterval(900)),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertTrue(model.weatherLine.contains("可能有雨"))
        XCTAssertTrue(model.weatherLine.contains("额外预留 10 分钟"))
        XCTAssertEqual(model.weatherState, .fresh)
        XCTAssertEqual(model.weatherTemperatureText, "20°")
        XCTAssertEqual(model.weatherRainText, "55%")
    }

    func testUnknownRainProbabilityIsNotRenderedAsZero() {
        let now = date(2026, 10, 23, 8, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800),
                               weatherStart: departure.addingTimeInterval(-1800),
                               weatherEnd: arrival.addingTimeInterval(900),
                               rain: -1, rainKnown: false),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertFalse(model.weatherLine.contains("%"))
        XCTAssertNil(model.weatherRainText)
        XCTAssertTrue(model.weatherLine.contains("暂无数据"))
    }

    func testOfflineWeatherIsStaleButKeepsLastValues() {
        let now = date(2026, 10, 23, 8, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800),
                               weatherStart: departure.addingTimeInterval(-1800),
                               weatherEnd: arrival.addingTimeInterval(900)),
            plan: plan(), syncState: .offline(now), demoMode: false, now: now
        )
        XCTAssertEqual(model.weatherState, .stale)
        XCTAssertEqual(model.weatherNotice, "更新失败，当前显示上次数据")
        XCTAssertEqual(model.weatherTemperatureText, "20°")
    }

    func testDemoSyncedStatusDoesNotClaimDataComplete() {
        let now = date(2026, 10, 23, 8, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: arrival.addingTimeInterval(1800)),
            plan: plan(), syncState: .synced(now), demoMode: true, now: now
        )
        XCTAssertEqual(model.statusText, "示例数据")
        XCTAssertFalse(model.statusText.contains("数据完整"))
    }

    func testDraftPhaseHasNoTimesAndPromptsSetup() {
        let model = CommutePresentation.make(
            advisory: nil, plan: nil, syncState: .localDraft, demoMode: false
        )
        XCTAssertEqual(model.phase, .draft)
        XCTAssertEqual(model.primaryActionTitle, "完成设置")
        XCTAssertTrue(model.arrivalLine.contains("服务器检查"))
    }

    func testExpiredAdvisoryBeforeArrivalShowsExpired() {
        let now = date(2026, 10, 24, 7, 0)
        let arrival = date(2026, 10, 24, 9, 0)
        let departure = date(2026, 10, 24, 8, 14)
        let model = CommutePresentation.make(
            advisory: advisory(serviceDate: arrival, arrival: arrival, departure: departure,
                               expires: now.addingTimeInterval(-60)),
            plan: plan(), syncState: .synced(now), demoMode: false, now: now
        )
        XCTAssertEqual(model.phase, .expired)
        XCTAssertEqual(model.statusText, "建议已过期")
        XCTAssertFalse(model.canNavigate)
    }
}
