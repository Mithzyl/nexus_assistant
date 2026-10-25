import XCTest
@testable import LifeAssistant

/// Contract fixtures captured from the real backend HTTP response (mock provider
/// mode). They do not contact the server, so normal App debugging stays offline.
final class CommuteContractFixtureTests: XCTestCase {
    private func fixtureData(_ name: String) throws -> Data {
        let bundle = Bundle(for: Self.self)
        guard let url = bundle.url(forResource: name, withExtension: "json") else {
            throw XCTSkip("fixture \(name).json not bundled")
        }
        return try Data(contentsOf: url)
    }

    func testDecodesCapturedBackendAdvisory() throws {
        let data = try fixtureData("advisory_backend_sample")
        let advisory = try CommuteJSON.decoder.decode(CommuteAdvisory.self, from: data)

        XCTAssertEqual(advisory.id, "fixture-advisory")
        XCTAssertEqual(advisory.quality, .fresh)
        XCTAssertEqual(advisory.departureStatus, .upcoming)
        XCTAssertEqual(advisory.etaSeconds, 1560)
        XCTAssertEqual(advisory.weatherExtraBufferSeconds, 600)
        XCTAssertEqual(advisory.weather?.forecastStart, CommuteJSON.parse("2026-10-23T09:14:00Z"))
        XCTAssertEqual(advisory.weather?.precipitationProbabilityMax, 0.55)
        XCTAssertEqual(advisory.weather?.precipitationProbabilityKnown, true)
        XCTAssertEqual(advisory.weather?.rainRisk, true)
        XCTAssertEqual(advisory.sources.first?.kind, "weather")
        XCTAssertEqual(advisory.sources.first?.provider, "mock")
        XCTAssertEqual(advisory.sources.first?.fetchedAt, CommuteJSON.parse("2026-10-23T07:48:15.715765Z"))
        XCTAssertEqual(advisory.reasonCodes, [
            "ROUTE_FRESH", "WEATHER_FRESH", "WEATHER_RAIN_THRESHOLD_MET", "DEPARTURE_UPCOMING"
        ])
        XCTAssertEqual(advisory.explanationSource, "template")
    }

    func testDecodesCheckWithNestedError() throws {
        let json = """
        {
          "check_id": "check-2",
          "plan_id": "plan-1",
          "status": "failed",
          "trigger": "auto",
          "plan_revision": 1,
          "advisory_id": "adv-1",
          "advisory_created": true,
          "error": {"code": "providers_unavailable",
                    "message": "天气和路线数据均不可用。",
                    "retryable": false},
          "created_at": "2026-10-23T00:00:00Z",
          "finished_at": "2026-10-23T00:00:02Z"
        }
        """.data(using: .utf8)!
        let check = try CommuteJSON.decoder.decode(CommuteCheck.self, from: json)
        XCTAssertEqual(check.status, .failed)
        XCTAssertEqual(check.errorCode, "providers_unavailable")
        XCTAssertEqual(check.error?.retryable, false)
        XCTAssertEqual(check.planRevision, 1)
        XCTAssertEqual(check.advisoryCreated, true)
    }

    func testDecodesUnavailableAdvisoryWithNulls() throws {
        let json = """
        {
          "id": "adv-null",
          "plan_id": "plan-1",
          "plan_revision": 1,
          "local_service_date": "2026-10-23",
          "generated_at": "2026-10-23T00:00:00Z",
          "expires_at": "2026-10-23T01:00:00Z",
          "quality": "unavailable",
          "title": "通勤建议暂不可用",
          "summary": "天气和路线数据暂时都不可用。",
          "target_arrival_at": "2026-10-23T01:00:00Z",
          "recommended_departure_at": null,
          "departure_status": "unknown",
          "eta_seconds": null,
          "buffer_seconds": 600,
          "weather_extra_buffer_seconds": 0,
          "weather": null,
          "sources": [{"kind":"weather","provider":"mock","fetched_at":null,
                       "valid_from":null,"valid_to":null,"freshness":"unavailable",
                       "attribution":null,"note":null}],
          "reason_codes": ["WEATHER_UNAVAILABLE", "ROUTE_UNAVAILABLE"],
          "explanation_source": "template"
        }
        """.data(using: .utf8)!
        let advisory = try CommuteJSON.decoder.decode(CommuteAdvisory.self, from: json)
        XCTAssertEqual(advisory.quality, .unavailable)
        XCTAssertNil(advisory.recommendedDepartureAt)
        XCTAssertNil(advisory.etaSeconds)
        XCTAssertNil(advisory.weather)
        XCTAssertNil(advisory.sources.first?.fetchedAt)
        XCTAssertEqual(advisory.sources.first?.freshness, "unavailable")
    }
}
