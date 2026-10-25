import Foundation

/// 实际 checks 请求体：`{plan_revision, route_snapshot}`（运行时 OpenAPI `CheckCreateRequest`）。
/// 一次提交固定 body 与 Idempotency-Key；重试必须复用同一份 body。
struct CheckCreateRequest: Codable, Equatable {
    let planRevision: Int
    let routeSnapshot: RouteSnapshotInput

    enum CodingKeys: String, CodingKey {
        case planRevision = "plan_revision"
        case routeSnapshot = "route_snapshot"
    }
}

struct RoutePointInput: Codable, Equatable {
    let latitude: Double
    let longitude: Double
}

struct RouteCandidateInput: Codable, Equatable {
    let routeReference: String
    let distanceMeters: Int?
    let durationSeconds: Int?
    let durationInTrafficSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case routeReference = "route_reference"
        case distanceMeters = "distance_meters"
        case durationSeconds = "duration_seconds"
        case durationInTrafficSeconds = "duration_in_traffic_seconds"
    }
}

struct RouteSnapshotInput: Codable, Equatable {
    let provider: String
    let origin: RoutePointInput
    let destination: RoutePointInput
    let transportMode: String
    let queryType: String
    let requestedDepartureAt: Date?
    let requestedArrivalAt: Date?
    let expectedDepartureAt: Date?
    let expectedArrivalAt: Date?
    let queriedAt: Date
    let distanceMeters: Int?
    let durationSeconds: Int?
    let durationInTrafficSeconds: Int?
    let etaSource: String?
    let etaScope: String?
    let routeReference: String?
    let candidateRoutes: [RouteCandidateInput]
    let sourceNote: String?

    enum CodingKeys: String, CodingKey {
        case provider, origin, destination
        case transportMode = "transport_mode"
        case queryType = "query_type"
        case requestedDepartureAt = "requested_departure_at"
        case requestedArrivalAt = "requested_arrival_at"
        case expectedDepartureAt = "expected_departure_at"
        case expectedArrivalAt = "expected_arrival_at"
        case queriedAt = "queried_at"
        case distanceMeters = "distance_meters"
        case durationSeconds = "duration_seconds"
        case durationInTrafficSeconds = "duration_in_traffic_seconds"
        case etaSource = "eta_source"
        case etaScope = "eta_scope"
        case routeReference = "route_reference"
        case candidateRoutes = "candidate_routes"
        case sourceNote = "source_note"
    }
}

/// 一次已发出但结果不明确的提交：保存完全相同的 body 与幂等键，重试/对账时原样复用。
struct PendingSubmission: Codable, Equatable {
    var planId: String
    var revision: Int
    var key: String
    var body: CheckCreateRequest?
    var createdAt: Date
    var uncertain: Bool
    var accountId: String
}

extension CommuteRouteSnapshot {
    /// MapKit 结果按后端字段提交；`calculateETA` 的总体耗时与候选路线自身耗时分开。
    func submission(plan: CommutePlan) -> CheckCreateRequest {
        let usesArrivalDeadline = targetArrivalAt != nil
        let candidateInputs = candidates.prefix(8).enumerated().map { index, candidate in
            RouteCandidateInput(
                routeReference: candidate.name ?? "candidate-\(index + 1)",
                distanceMeters: candidate.distanceMeters.isFinite
                    ? Int(candidate.distanceMeters.rounded()) : nil,
                // 候选路线只提交它自己的路线耗时，不复制总体交通 ETA。
                durationSeconds: candidate.routeExpectedTravelSeconds,
                durationInTrafficSeconds: nil
            )
        }
        let snapshot = RouteSnapshotInput(
            provider: "mapkit_client",
            origin: RoutePointInput(latitude: plan.origin.latitude, longitude: plan.origin.longitude),
            destination: RoutePointInput(latitude: plan.destination.latitude, longitude: plan.destination.longitude),
            transportMode: plan.transportMode.rawValue,
            queryType: usesArrivalDeadline ? "arrival_deadline" : "departure_time",
            requestedDepartureAt: usesArrivalDeadline ? nil : (etaDepartureAt ?? queriedAt),
            requestedArrivalAt: usesArrivalDeadline ? targetArrivalAt : nil,
            expectedDepartureAt: etaDepartureAt,
            expectedArrivalAt: etaArrivalAt,
            queriedAt: queriedAt,
            distanceMeters: etaDistanceMeters.map { Int($0.rounded()) },
            durationSeconds: candidates.first?.routeExpectedTravelSeconds,
            durationInTrafficSeconds: etaSeconds,
            etaSource: etaSeconds == nil ? nil : "calculate_eta",
            etaScope: etaSeconds == nil ? nil : "overall",
            routeReference: primaryRouteName,
            candidateRoutes: candidateInputs,
            sourceNote: "本机 MapKit 查询结果，非 Apple 服务端签名。"
        )
        return CheckCreateRequest(planRevision: plan.revision, routeSnapshot: snapshot)
    }
}
