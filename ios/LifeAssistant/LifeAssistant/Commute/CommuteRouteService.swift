import Foundation
import MapKit

/// 一条候选项的路线事实。`routeExpectedTravelSeconds` 来自 MKRoute，是路线自身的
/// 静态估算；它**不是**按到达时间查询的交通 ETA，不能与 MKDirections.ETAResponse 混用。
struct CommuteRouteCandidate: Codable, Equatable {
    let name: String?
    let distanceMeters: Double
    let routeExpectedTravelSeconds: Int
    let pointCount: Int
}

/// 本机采集的 MapKit 路线快照。后端 checks 接口目前不接受该结构（见
/// ios/LifeAssistant/docs/commute-route-snapshot-contract.md），因此只在本机展示，
/// 不冒充已提交给服务器。
struct CommuteRouteSnapshot: Codable, Equatable {
    let provider: String
    let transport: String
    let queriedAt: Date
    let targetArrivalAt: Date?
    /// MKDirections.ETAResponse.expectedTravelTime：按目标到达时间查询的交通预计耗时。
    let etaSeconds: Int?
    let etaArrivalAt: Date?
    let etaDepartureAt: Date?
    let etaDistanceMeters: Double?
    /// 主路线（MKRoute）自己的距离与名称，用于展示候选；不当作 ETA。
    let primaryRouteName: String?
    let primaryRouteDistanceMeters: Double?
    let candidates: [CommuteRouteCandidate]
}

enum CommuteRouteError: LocalizedError, Equatable {
    case missingCoordinates
    case throttled
    case notFound
    case serviceUnavailable

    var errorDescription: String? {
        switch self {
        case .missingCoordinates: return "起终点坐标不完整，无法查询路线。"
        case .throttled: return "地图查询过于频繁，稍后再试。"
        case .notFound: return "地图没有返回可用路线。"
        case .serviceUnavailable: return "地图服务暂时不可用。"
        }
    }
}

protocol CommuteRouteService {
    func fetchRoute(origin: GeoPoint,
                    destination: GeoPoint,
                    mode: TransportMode,
                    arrivalBy: Date?) async throws -> CommuteRouteSnapshot
}

/// 真实 MapKit：路线几何用 calculate()，交通相关耗时只用 calculateETA() 的 MKDirections.ETAResponse。
final class MapKitRouteService: CommuteRouteService {
    func fetchRoute(origin: GeoPoint,
                    destination: GeoPoint,
                    mode: TransportMode,
                    arrivalBy: Date?) async throws -> CommuteRouteSnapshot {
        guard CLLocationCoordinate2DIsValid(origin.location),
              CLLocationCoordinate2DIsValid(destination.location),
              !(origin.latitude == 0 && origin.longitude == 0),
              !(destination.latitude == 0 && destination.longitude == 0) else {
            throw CommuteRouteError.missingCoordinates
        }

        let routeRequest = request(origin: origin, destination: destination, mode: mode, arrivalBy: arrivalBy)
        // 路线查询不设置到达时间：它只返回几何与静态估算，避免把路线耗时误当交通 ETA。
        routeRequest.arrivalDate = nil
        routeRequest.departureDate = nil

        let routeResult = try await calculate(routeRequest, mode: mode)
        guard let primary = routeResult.routes.first else { throw CommuteRouteError.notFound }

        let etaRequest = request(origin: origin, destination: destination, mode: mode, arrivalBy: arrivalBy)
        let eta: MKDirections.ETAResponse
        do {
            eta = try await calculateETA(etaRequest)
        } catch {
            throw Self.mapError(error)
        }

        let now = Date()
        let candidates = routeResult.routes.prefix(3).map { route in
            CommuteRouteCandidate(
                name: route.name,
                distanceMeters: route.distance,
                routeExpectedTravelSeconds: Int(route.expectedTravelTime.rounded()),
                pointCount: route.polyline.pointCount
            )
        }

        return CommuteRouteSnapshot(
            provider: "mapkit",
            transport: mode == .driving ? "automobile" : "walking",
            queriedAt: now,
            targetArrivalAt: arrivalBy,
            etaSeconds: Int(eta.expectedTravelTime.rounded()),
            etaArrivalAt: eta.expectedArrivalDate,
            etaDepartureAt: eta.expectedDepartureDate,
            etaDistanceMeters: eta.distance,
            primaryRouteName: primary.name,
            primaryRouteDistanceMeters: primary.distance,
            candidates: candidates
        )
    }

    // MARK: - 请求

    private func request(origin: GeoPoint, destination: GeoPoint,
                         mode: TransportMode, arrivalBy: Date?) -> MKDirections.Request {
        let request = MKDirections.Request()
        request.source = MKMapItem(placemark: MKPlacemark(coordinate: origin.location))
        request.destination = MKMapItem(placemark: MKPlacemark(coordinate: destination.location))
        request.transportType = mode == .driving ? .automobile : .walking
        request.requestsAlternateRoutes = mode == .driving
        if mode == .driving {
            // 驾车才有按到达时间计算的交通 ETA；步行是静态耗时。
            if let arrivalBy {
                request.arrivalDate = arrivalBy
            } else {
                request.departureDate = Date()
            }
        }
        return request
    }

    private func calculate(_ request: MKDirections.Request, mode: TransportMode) async throws -> MKDirections.Response {
        let directions = MKDirections(request: request)
        do {
            return try await withTaskCancellationHandler {
                try await directions.calculate()
            } onCancel: {
                directions.cancel()
            }
        } catch {
            throw Self.mapError(error)
        }
    }

    private func calculateETA(_ request: MKDirections.Request) async throws -> MKDirections.ETAResponse {
        let directions = MKDirections(request: request)
        return try await withTaskCancellationHandler {
            try await directions.calculateETA()
        } onCancel: {
            directions.cancel()
        }
    }

    private static func mapError(_ error: Error) -> CommuteRouteError {
        if error is CancellationError { return .serviceUnavailable }
        guard let mkError = error as? MKError else {
            let nsError = error as NSError
            if nsError.domain == MKErrorDomain { return .serviceUnavailable }
            return .serviceUnavailable
        }
        switch mkError.code {
        case .loadingThrottled:
            return .throttled
        case .placemarkNotFound, .directionsNotFound:
            return .notFound
        default:
            return .serviceUnavailable
        }
    }
}

extension GeoPoint {
    var location: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }

    var hasUsableCoordinates: Bool {
        CLLocationCoordinate2DIsValid(location) && !(latitude == 0 && longitude == 0)
    }
}
