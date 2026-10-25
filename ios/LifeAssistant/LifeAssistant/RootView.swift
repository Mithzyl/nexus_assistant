import SwiftUI

struct RootView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var commute: CommuteStore

    private var advisoryPublisher: NotificationCenter.Publisher {
        NotificationCenter.default.publisher(for: .commuteOpenAdvisory)
    }

    var body: some View {
        NavigationStack(path: $state.path) {
            TabView(selection: $state.tab) {
                TodayView()
                    .tabItem { Label(Tab.today.title, systemImage: Tab.today.icon) }
                    .tag(Tab.today)
                MattersView()
                    .tabItem { Label(Tab.matters.title, systemImage: Tab.matters.icon) }
                    .tag(Tab.matters)
                AskView()
                    .tabItem { Label(Tab.ask.title, systemImage: Tab.ask.icon) }
                    .tag(Tab.ask)
                MeView()
                    .tabItem { Label(Tab.me.title, systemImage: Tab.me.icon) }
                    .tag(Tab.me)
            }
            .tint(Palette.fog)
            .background(Palette.warm)
            .navigationDestination(for: Route.self) { route in
                RouteView(route: route)
            }
            .task {
                #if DEBUG
                guard state.path.isEmpty,
                      let raw = ProcessInfo.processInfo.environment["START_ROUTE"] else { return }
                switch raw {
                case "dataSources": state.open(.dataSources)
                case "noteImport": state.open(.noteImport)
                case "calendar": state.open(.sourceDetail("calendar"))
                case "reminders": state.open(.sourceDetail("reminders"))
                default: break
                }
                #endif
            }
        }
        .onReceive(advisoryPublisher) { note in
            guard let advisoryId = note.object as? String else { return }
            // 未登录时先回到设置页完成登录，避免用过期/他人的通知直接进入详情。
            state.open(commute.isSignedIn ? .commuteAdvisory(advisoryId) : .commuteSettings)
        }
        .sheet(isPresented: $state.showCapture) {
            CaptureSheet(preset: state.capturePreset)
                .presentationDragIndicator(.visible)
        }
    }
}

struct RouteView: View {
    let route: Route

    var body: some View {
        switch route {
        case .captureSource(let id): CaptureSourceView(sourceId: id)
        case .source(let id): SourceView(evidenceId: id)
        case .refund: RefundView()
        case .shelf: ShelfView()
        case .ambiguity: AmbiguityView()
        case .prepare: PrepareView()
        case .ticket: TicketView()
        case .wish: WishView()
        case .wishPlan: WishPlanView()
        case .project: ProjectView()
        case .travel: TravelView()
        case .added(let id): AddedView(matterId: id)
        case .memories: MemoriesView()
        case .memory(let id): MemoryDetailView(memoryId: id)
        case .lesson: LessonView()
        case .privacy: PrivacyView()
        case .setup: SetupView()
        case .info: InfoView()
        case .commuteSettings: CommuteSettingsView()
        case .commuteAdvisory(let id): CommuteAdvisoryView(advisoryId: id)
        case .commuteNotifications: CommuteNotificationsView()
        case .dataSources: DataSourcesView()
        case .sourceDetail(let kind):
            SourceDetailView(kind: SourceKind(rawValue: kind) ?? .calendar)
        case .noteImport: NoteImportView()
        }
    }
}

/// Pushed page shell: custom header with a back chevron, warm background.
struct PageShell<Content: View>: View {
    let title: String
    var subtitle: String? = nil
    var trailing: String? = nil
    var trailingAction: (title: String, action: () -> Void)? = nil
    @ViewBuilder var content: () -> Content
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.stack) {
                if let subtitle {
                    Text(subtitle)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .padding(.horizontal, Spacing.screen)
                }
                content()
                    .padding(.horizontal, Spacing.screen)
                    .padding(.bottom, 32)
            }
            .padding(.top, 6)
        }
        .scrollIndicators(.hidden)
        .background(Palette.warm)
        .navigationBarBackButtonHidden(true)
        .toolbar(.hidden, for: .navigationBar)
        .safeAreaInset(edge: .top, spacing: 0) {
            HStack(spacing: 12) {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Palette.fogDeep)
                        .frame(width: 44, height: 44)
                        .contentShape(Circle())
                }
                .chromeGlass(in: Circle())
                .buttonStyle(.plain)
                .accessibilityIdentifier("nav.back")
                .accessibilityLabel("返回")

                Text(title)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)

                Spacer()

                if let trailingAction {
                    Button(trailingAction.title) {
                        trailingAction.action()
                    }
                    .font(.system(.subheadline, weight: .semibold))
                    .foregroundStyle(Palette.fogDeep)
                    .frame(minHeight: 44)
                } else if let trailing {
                    Text(trailing)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }
            }
            .padding(.horizontal, Spacing.screen)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial)
            .overlay(alignment: .bottom) {
                Rectangle().fill(Palette.line).frame(height: 0.5)
            }
        }
    }
}
