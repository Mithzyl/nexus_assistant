import SwiftUI
import MapKit
import CoreLocation

/// 搜索补全包装。
@MainActor
final class PlaceSearchCompleter: NSObject, ObservableObject, MKLocalSearchCompleterDelegate {
    @Published var results: [MKLocalSearchCompletion] = []
    private let completer = MKLocalSearchCompleter()

    override init() {
        super.init()
        completer.delegate = self
        completer.resultTypes = [.address, .pointOfInterest]
    }

    func update(query: String) {
        completer.queryFragment = query
        if query.isEmpty { results = [] }
    }

    nonisolated func completerDidUpdateResults(_ completer: MKLocalSearchCompleter) {
        let values = completer.results
        Task { @MainActor in self.results = values }
    }

    nonisolated func completer(_ completer: MKLocalSearchCompleter, didFailWithError error: Error) {
        Task { @MainActor in self.results = [] }
    }
}

/// 只在用户点“使用当前位置”时请求 when-in-use 权限，绝不申请始终定位。
@MainActor
final class CurrentLocationProvider: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var coordinate: CLLocationCoordinate2D?
    @Published var denied = false
    @Published var isLocating = false

    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func request() {
        isLocating = true
        switch manager.authorizationStatus {
        case .notDetermined:
            manager.requestWhenInUseAuthorization()
        case .denied, .restricted:
            denied = true
            isLocating = false
        default:
            manager.requestLocation()
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            switch status {
            case .authorizedWhenInUse, .authorizedAlways:
                manager.requestLocation()
            case .denied, .restricted:
                self.denied = true
                self.isLocating = false
            default:
                break
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let coordinate = locations.last?.coordinate else { return }
        Task { @MainActor in
            self.coordinate = coordinate
            self.isLocating = false
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.isLocating = false }
    }
}

struct PlacePickerView: View {
    var initial: GeoPoint?
    var onPick: (GeoPoint) -> Void

    @Environment(\.dismiss) private var dismiss
    @StateObject private var completer = PlaceSearchCompleter()
    @StateObject private var location = CurrentLocationProvider()
    @State private var query = ""
    @State private var label = ""
    @State private var selection: CLLocationCoordinate2D?
    @State private var camera: MapCameraPosition = .region(
        MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: 24.3135, longitude: 109.4132),
            span: MKCoordinateSpan(latitudeDelta: 0.03, longitudeDelta: 0.03)
        )
    )
    @State private var resolving = false

    var body: some View {
        PageShell(title: "选择地点", subtitle: "搜索地点，或在地图上点选") {
            VStack(alignment: .leading, spacing: 14) {
                searchField

                if !completer.results.isEmpty && !query.isEmpty {
                    SurfaceCard(padding: 6) {
                        VStack(spacing: 0) {
                            ForEach(completer.results.prefix(5), id: \.self) { item in
                                Button {
                                    resolve(item)
                                } label: {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.title.isEmpty ? item.subtitle : item.title)
                                            .font(.cardTitle)
                                            .foregroundStyle(Palette.ink)
                                            .multilineTextAlignment(.leading)
                                        if !item.subtitle.isEmpty {
                                            Text(item.subtitle)
                                                .font(.captionText)
                                                .foregroundStyle(Palette.muted)
                                                .multilineTextAlignment(.leading)
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.vertical, 10)
                                    .padding(.horizontal, 8)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }

                mapCard

                HStack(spacing: 10) {
                    SecondaryButton(title: location.isLocating ? "正在定位…" : "使用当前位置") {
                        location.request()
                    }
                    if location.denied {
                        Text("定位被拒绝，仍可搜索或点选地图。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                }
                .onChange(of: location.coordinate?.latitude) {
                    guard let coordinate = location.coordinate else { return }
                    selection = coordinate
                    camera = .region(MKCoordinateRegion(center: coordinate,
                                                        span: MKCoordinateSpan(latitudeDelta: 0.01, longitudeDelta: 0.01)))
                    if label.isEmpty { label = "当前位置" }
                }

                labelField

                PrimaryButton(title: resolving ? "正在确认地点…" : "确认这个地点",
                              enabled: selection != nil && !label.trimmingCharacters(in: .whitespaces).isEmpty) {
                    guard let selection else { return }
                    onPick(GeoPoint(label: label.trimmingCharacters(in: .whitespacesAndNewlines),
                                    latitude: selection.latitude,
                                    longitude: selection.longitude))
                    dismiss()
                }

                Text("只在你主动点击“使用当前位置”时申请定位权限；不会后台持续定位。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onAppear {
            if let initial {
                selection = CLLocationCoordinate2D(latitude: initial.latitude, longitude: initial.longitude)
                label = initial.label
                camera = .region(MKCoordinateRegion(
                    center: CLLocationCoordinate2D(latitude: initial.latitude, longitude: initial.longitude),
                    span: MKCoordinateSpan(latitudeDelta: 0.01, longitudeDelta: 0.01)))
            }
        }
    }

    private var searchField: some View {
        TextField("搜索地点，例如公司或家", text: $query)
            .font(.bodyText)
            .padding(12)
            .background(Palette.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Palette.line, lineWidth: 0.5))
            .onChange(of: query) { _, value in completer.update(query: value) }
            .submitLabel(.search)
            .accessibilityLabel("搜索地点")
    }

    private var mapCard: some View {
        MapReader { proxy in
            Map(position: $camera) {
                if let selection {
                    Marker(label.isEmpty ? "选定地点" : label, coordinate: selection)
                }
            }
            .frame(height: 260)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Palette.line, lineWidth: 0.5))
            .onTapGesture { point in
                if let coordinate = proxy.convert(point, from: .local) {
                    selection = coordinate
                }
            }
            .accessibilityLabel("地图，点按可选定坐标")
        }
    }

    private var labelField: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("名称")
                .font(.microLabel)
                .foregroundStyle(Palette.muted)
            TextField("例如：家、公司", text: $label)
                .font(.bodyText)
                .padding(12)
                .background(Palette.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Palette.line, lineWidth: 0.5))
        }
    }

    private func resolve(_ completion: MKLocalSearchCompletion) {
        resolving = true
        let request = MKLocalSearch.Request(completion: completion)
        Task {
            defer { resolving = false }
            guard let response = try? await MKLocalSearch(request: request).start(),
                  let item = response.mapItems.first else { return }
            let coordinate = item.placemark.coordinate
            selection = coordinate
            label = completion.title.isEmpty ? completion.subtitle : completion.title
            camera = .region(MKCoordinateRegion(center: coordinate,
                                                span: MKCoordinateSpan(latitudeDelta: 0.01, longitudeDelta: 0.01)))
        }
    }
}
