import SwiftUI
import MapKit

enum CommuteNavigator {
    static func open(plan: CommutePlan) {
        let placemark = MKPlacemark(coordinate: CLLocationCoordinate2D(
            latitude: plan.destination.latitude, longitude: plan.destination.longitude))
        let item = MKMapItem(placemark: placemark)
        item.name = plan.destination.label
        item.openInMaps(launchOptions: [
            MKLaunchOptionsDirectionsModeKey:
                plan.transportMode == .driving
                ? MKLaunchOptionsDirectionsModeDriving
                : MKLaunchOptionsDirectionsModeWalking
        ])
    }
}

struct CommuteAdvisoryView: View {
    let advisoryId: String?

    @EnvironmentObject private var store: CommuteStore
    @EnvironmentObject private var state: AppState
    @State private var refreshing = false
    @State private var loadedId: String?
    @State private var showBreakdown = false
    @State private var showDataDetails = false

    private var advisory: CommuteAdvisory? {
        if let advisoryId, !advisoryId.isEmpty {
            if store.openedAdvisory?.id == advisoryId { return store.openedAdvisory }
            if store.advisory?.id == advisoryId { return store.advisory }
            if store.historicalAdvisory?.id == advisoryId { return store.historicalAdvisory }
            return nil
        }
        return store.advisory
    }

    private var plan: CommutePlan? { store.plan }

    private var isHistorical: Bool {
        guard let advisory else { return false }
        return store.isHistorical(advisory)
    }

    private var model: CommutePresentation {
        .make(advisory: advisory, plan: plan, syncState: store.syncState,
              demoMode: store.demoMode, historicalOverride: isHistorical)
    }

    private var timezone: TimeZone {
        TimeZone(identifier: plan?.timezone ?? "Asia/Shanghai") ?? .current
    }

    var body: some View {
        PageShell(
            title: plan?.name ?? "通勤建议",
            subtitle: routeLine,
            trailingAction: plan == nil ? nil : ("编辑", { state.open(.commuteSettings) })
        ) {
            VStack(alignment: .leading, spacing: 16) {
                if !isHistorical {
                    // 页面停留时按单调时钟重算心跳年龄；不会因此发网络请求。
                    TimelineView(.periodic(from: .now, by: 5)) { _ in
                        if let hint = store.runtimeHint {
                            runtimeHintCard(hint)
                        }
                    }
                }
                if let advisory {
                    if isHistorical || model.phase == .dayPassed || model.phase == .expired {
                        staleBanner()
                    }
                    // 处理状态只属于当前计划的分析；历史详情不展示当前等待，避免混淆。
                    if !isHistorical, let status = store.processingStateText {
                        statusBanner(status)
                    }
                    if let notice = store.planUpdatedNotice, !isHistorical {
                        planUpdatedBanner(notice)
                    }
                    coreCard(advisory)
                    latestWeatherCard(advisory)
                    breakdownCard(advisory)
                    aiCard(advisory)
                    weatherCard(advisory)
                    dataDetails(advisory)
                    actions(advisory)
                } else if store.syncState == .syncing {
                    ProgressView("正在读取建议…")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 40)
                } else {
                    emptyState
                }
                routeCard()
            }
        }
        .task {
            // 只读读取最新天气与后台运行状态；不会因为打开页面而创建分析。
            await store.refreshWeather()
            await store.refreshRuntime(force: true)
            guard let advisoryId, !advisoryId.isEmpty, loadedId != advisoryId else { return }
            loadedId = advisoryId
            await store.loadAdvisory(id: advisoryId)
            await store.reportOpened(advisoryId: advisoryId)
        }
        .onChange(of: store.checkStatus) { _, status in
            // 任务开始/变化时补一次心跳读取；有 TTL 和共享请求，不会形成第二个高频计时器。
            guard let status, !status.isTerminal else { return }
            Task { await store.refreshRuntime() }
        }
    }

    private var routeLine: String {
        guard let plan else { return "服务器生成的结果" }
        let day = model.dayLabel
        return "\(plan.origin.label) → \(plan.destination.label) · \(plan.transportMode.title)" +
               (day.isEmpty ? "" : " · \(day) \(plan.arrivalLocalTime) 前到")
    }

    private func coreCard(_ advisory: CommuteAdvisory) -> some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text(model.headline)
                    .font(.system(.title2, design: .rounded).weight(.semibold))
                    .foregroundStyle(Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)
                // 优先给结果事实：到达、路程、总预留；旧结果没有拆分时也只给总数。
                HStack(alignment: .top, spacing: 18) {
                    metric("目标到达", CommuteFormat.clock(advisory.targetArrivalAt, timeZone: timezone))
                    metric("路程", advisory.etaSeconds.map(CommuteFormat.duration) ?? "缺少数据")
                    metric("预留", CommuteFormat.duration(advisory.bufferSeconds + advisory.weatherExtraBufferSeconds))
                }
                if let weatherUpdated = model.weatherUpdatedText {
                    Text(weatherUpdated)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }
                if !model.weatherLine.isEmpty {
                    Text(model.weatherLine)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack {
                    Text(model.updatedText ?? "")
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                    Spacer()
                    Text(model.statusText)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }
            }
        }
    }

    private func breakdownCard(_ advisory: CommuteAdvisory) -> some View {
        let totalBuffer = advisory.bufferSeconds + advisory.weatherExtraBufferSeconds
        return SurfaceCard {
            VStack(alignment: .leading, spacing: 10) {
                Text("时间拆解")
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                HStack {
                    Text("路程").font(.captionText).foregroundStyle(Palette.muted)
                    Spacer()
                    Text(advisory.etaSeconds.map(CommuteFormat.duration) ?? "缺少数据")
                        .font(.system(.subheadline, weight: .medium))
                        .foregroundStyle(advisory.etaSeconds == nil ? Palette.amber : Palette.ink)
                }
                HStack {
                    Text("总预留").font(.captionText).foregroundStyle(Palette.muted)
                    Spacer()
                    Text(CommuteFormat.duration(totalBuffer))
                        .font(.system(.subheadline, weight: .medium))
                        .foregroundStyle(Palette.ink)
                }
                DisclosureGroup(isExpanded: $showBreakdown) {
                    VStack(alignment: .leading, spacing: 6) {
                        detailLine("固定预留", CommuteFormat.duration(advisory.bufferSeconds))
                        detailLine("天气额外预留", CommuteFormat.duration(advisory.weatherExtraBufferSeconds))
                        detailLine("目标到达", CommuteFormat.clock(advisory.targetArrivalAt, timeZone: timezone))
                    }
                    .padding(.top, 6)
                } label: {
                    Text("明细").font(.captionText).foregroundStyle(Palette.fogDeep)
                }
            }
        }
    }

    /// 服务端返回的智能分析：状态、采用的是哪种耗时、预留、依据与缺失信息。
    /// 建议已过期时明确标为历史结果，不能当作当前实时出行建议。
    private func aiCard(_ advisory: CommuteAdvisory) -> some View {
        let model = self.model
        let weatherSource = advisory.sources.first { ($0.kind ?? "") == "weather" }
        let routeSource = advisory.sources.first { ($0.kind ?? "") == "route" }
        return SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("智能分析")
                        .font(.cardTitle)
                        .foregroundStyle(Palette.ink)
                    Spacer()
                    if model.aiIsHistorical {
                        Text("历史结果")
                            .font(.microLabel)
                            .foregroundStyle(Palette.amber)
                    }
                }

                if let ai = advisory.aiAnalysis {
                    Text((model.aiStatusText ?? ai.status.title) + (model.aiIsHistorical ? "（历史）" : ""))
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(model.aiIsHistorical ? Palette.amber
                                         : (ai.status == .available ? Palette.ink : Palette.amber))
                    if model.aiIsHistorical {
                        Text("这是历史建议，不代表现在该出发；请按当前时间与当前计划重新核实。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let departure = ai.recommendedDepartureAt ?? advisory.recommendedDepartureAt {
                        detailLine("建议出发", CommuteFormat.clock(departure, timeZone: timezone))
                    }
                    detailLine("耗时来源", ai.durationSource.title)
                    if let used = ai.usedDurationSeconds {
                        detailLine("实际采用", CommuteFormat.duration(used))
                    }
                    if let map = ai.mapDurationSeconds {
                        detailLine("地图耗时", CommuteFormat.duration(map))
                    }
                    detailLine("模型额外预留",
                               ai.extraBufferSeconds.map(CommuteFormat.duration) ?? "未单独记录")
                    if !advisory.summary.isEmpty {
                        Text("简短依据")
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                        Text(advisory.summary)
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !ai.missingInformation.isEmpty {
                        Text("服务器标注 \(ai.missingInformation.count) 项信息缺失；不会据此推断实时路况或到达时间。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Text("服务器没有返回智能分析；下方仍显示路线耗时与天气事实。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if let fetched = weatherSource?.fetchedAt {
                    detailLine("天气获取", CommuteFormat.clock(fetched, timeZone: timezone))
                }
                if routeSource?.freshness == "unavailable" {
                    detailLine("服务端路线", "不可用（本次采用客户端快照）")
                } else if let routeSource {
                    detailLine("路线获取", routeSource.fetchedAt.map { CommuteFormat.clock($0, timeZone: timezone) } ?? "未知")
                }
            }
        }
    }

    /// 独立天气快照：只描述“现在拿到的最新预报”，不覆盖某条旧建议采用的天气。
    private func latestWeatherCard(_ advisory: CommuteAdvisory) -> some View {
        let latest = store.latestWeather
        let advisoryWeatherFetched = advisory.sources.first { ($0.kind ?? "") == "weather" }?.fetchedAt
        let isNewer: Bool = {
            guard let latestFetched = latest?.fetchedAt else { return false }
            guard let advisoryFetched = advisoryWeatherFetched else { return true }
            return latestFetched > advisoryFetched.addingTimeInterval(60)
        }()
        return SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("最新天气")
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                if let latest {
                    if let summary = latest.summary {
                        HStack(spacing: 18) {
                            if let temperature = summary.temperatureC {
                                metric("温度", String(format: "%.0f°", temperature))
                            }
                            if summary.precipitationProbabilityKnown != false,
                               let rain = summary.precipitationProbabilityMax {
                                metric("降雨概率（预报）", "\(Int((rain * 100).rounded()))%")
                            }
                        }
                        if let from = latest.validFrom, let to = latest.validTo {
                            Text("预报时段 \(CommuteFormat.clock(from, timeZone: timezone))–\(CommuteFormat.clock(to, timeZone: timezone))")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                        }
                    } else {
                        Text("服务器没有返回天气摘要。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                    if !latest.matchesCurrentPlan {
                        Text("这条天气对应修改前的计划，不适用于当前设置。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                    }
                    if latest.isStale {
                        Text("天气已超出新鲜度窗口，仅供回看。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                    }
                    if let fetched = latest.fetchedAt {
                        Text("获取于 \(CommuteFormat.dayTime(fetched))")
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                    }
                    if isNewer {
                        Text("天气已更新，下面的建议仍是上次分析的结果。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else if let notice = store.weatherNotice {
                    Text(notice)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                } else {
                    Text("还没有读取到独立天气记录。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                }
            }
        }
    }

    private func weatherCard(_ advisory: CommuteAdvisory) -> some View {
        let covered = CommutePresentation.weatherCoversCommute(advisory, timezone: timezone)
        let source = advisory.sources.first { ($0.kind ?? "") == "weather" }
        let stale: Bool = {
            if case .offline = store.syncState { return true }
            if case .failed = store.syncState { return true }
            return false
        }()
        let rainKnown = covered && advisory.weather?.precipitationProbabilityKnown != false
        let rain = rainKnown ? advisory.weather?.rainProbability : nil
        return SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("通勤时段天气")
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                if covered, let weather = advisory.weather {
                    if stale {
                        Text("更新失败，当前显示上次数据")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                    }
                    if case .syncing = store.syncState {
                        Text("正在更新天气…")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                    HStack(spacing: 18) {
                        if let temperature = weather.temperatureC {
                            metric("行程时段温度", String(format: "%.0f°", temperature))
                        }
                        if let rain {
                            metric("降雨概率", "\(Int((rain * 100).rounded()))%")
                        }
                    }
                    if let location = store.plan?.destination.label, !location.isEmpty {
                        Text("地点：\(location)")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                    if let from = weather.validFrom, let to = weather.validTo {
                        Text("预报时段 \(CommuteFormat.clock(from, timeZone: timezone))–\(CommuteFormat.clock(to, timeZone: timezone))，覆盖本次通勤")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !rainKnown {
                        Text("降雨概率暂无数据，未按 0 处理。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                    }
                    if let fetchedAt = source?.fetchedAt {
                        Text("天气更新于 \(CommuteFormat.dayTime(fetchedAt))")
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                    }
                    if let attribution = source?.attribution,
                       let urlString = attribution.url, let url = URL(string: urlString) {
                        Link(destination: url) {
                            Text(attribution.text ?? "天气数据来源与归属")
                                .font(.captionText)
                                .underline()
                        }
                    }
                } else {
                    Text("天气暂不可用")
                        .font(.bodyText)
                        .foregroundStyle(Palette.ink)
                    Text("没有覆盖出发到到达的预报，因此没有用它解释这次通勤。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func dataDetails(_ advisory: CommuteAdvisory) -> some View {
        SurfaceCard {
            DisclosureGroup(isExpanded: $showDataDetails) {
                VStack(alignment: .leading, spacing: 8) {
                    detailLine("生成于", CommuteFormat.dayTime(advisory.generatedAt))
                    detailLine("有效期至", CommuteFormat.dayTime(advisory.expiresAt))
                    detailLine("数据状态", advisory.quality.title)
                    ForEach(advisory.sources) { source in
                        VStack(alignment: .leading, spacing: 1) {
                            Text(providerTitle(source.provider))
                                .font(.microLabel)
                                .foregroundStyle(Palette.ink)
                            Text("获取于 \(source.fetchedAt.map { CommuteFormat.clock($0, timeZone: timezone) } ?? "时间未知") · \(freshnessTitle(source.freshness))")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                    if advisory.etaSeconds == nil {
                        Text("缺少路况耗时：没有可靠 ETA 时不推算出发时间。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                    }
                    if advisory.weather == nil {
                        Text("缺少天气数据：本次没有使用天气推断。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                    }
                }
                .padding(.top, 6)
            } label: {
                Text("数据与计算说明")
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
            }
        }
    }

    private func actions(_ advisory: CommuteAdvisory) -> some View {
        VStack(spacing: 10) {
            if model.phase == .actionable, let plan {
                PrimaryButton(title: "开始导航") { CommuteNavigator.open(plan: plan) }
            } else if let plan {
                SecondaryButton(title: "查看路线") { CommuteNavigator.open(plan: plan) }
            }

            PrimaryButton(title: routeActionTitle,
                          enabled: !store.routeState.isFetching && !store.isRefreshing
                            && store.pendingSubmission == nil,
                          identifier: "commute.updateRoute") {
                Task { await store.updateRouteAndAnalyze() }
            }
            .accessibilityIdentifier("commute.updateRoute")

            if store.pendingSubmission == nil {
                Text("重新分析会重新查询 MapKit 路线并提交一次服务器分析；只看已有结果时不必点它。")
                    .font(.microLabel)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack(spacing: 10) {
                SecondaryButton(title: refreshing ? "读取中…" : "刷新处理状态") {
                    refreshing = true
                    Task {
                        await store.refresh(planId: advisory.planId)
                        refreshing = false
                    }
                }
                SecondaryButton(title: "今天不再提醒") {
                    Task { await store.skipToday() }
                }
            }

            if !store.isSignedIn {
                Text("未登录服务器：只显示本地缓存，刷新与提醒需要登录。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
            }
        }
    }

    private var routeActionTitle: String {
        if store.routeState.isFetching { return "正在获取路线…" }
        if store.isRefreshing { return "正在分析…" }
        if store.pendingSubmission?.uncertain == true { return "先核对上次提交" }
        if store.pendingSubmission != nil { return "已提交，等待服务器分析" }
        return "重新分析"
    }

    /// 本机 MapKit 结果与服务器建议分开展示：来源、时间、路线耗时口径都写清楚。
    private func routeCard() -> some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("本机路线与交通")
                        .font(.cardTitle)
                        .foregroundStyle(Palette.ink)
                    Spacer()
                    Text("MapKit")
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }

                if let pending = store.pendingSubmission {
                    HStack(spacing: 8) {
                        Text(pending.uncertain ? "上次提交结果未知" : "已提交，等待服务器分析")
                            .font(.captionText)
                            .foregroundStyle(pending.uncertain ? Palette.amber : Palette.muted)
                        Spacer()
                        if pending.uncertain {
                            Button("核对上次提交") {
                                Task { await store.reconcilePendingSubmission() }
                            }
                            .font(.system(.subheadline, weight: .semibold))
                            .foregroundStyle(Palette.fogDeep)
                            .accessibilityIdentifier("commute.reconcile")
                        }
                    }
                    if pending.uncertain {
                        Text("核对会原样复用同一份路线快照与幂等键；服务器按幂等键重放，不会新建一次分析。")
                            .font(.captionText)
                            .foregroundStyle(Palette.faint)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                switch store.routeState {
                case .fetching:
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("正在向地图查询路线与到达时间…")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                case .failed(let message):
                    Text(message)
                        .font(.captionText)
                        .foregroundStyle(Palette.amber)
                    Text("路线查询失败时不会提交路线快照，也没有路线 AI 分析；服务器仍可按计划单独生成天气或“无路线”降级建议。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                case .idle, .ready:
                    if let snapshot = store.routeSnapshot {
                        HStack {
                            Text("地图预计耗时").font(.captionText).foregroundStyle(Palette.muted)
                            Spacer()
                            Text(snapshot.etaSeconds.map(CommuteFormat.duration) ?? "暂无")
                                .font(.system(.subheadline, weight: .medium))
                                .foregroundStyle(snapshot.etaSeconds == nil ? Palette.amber : Palette.ink)
                        }
                        if let distance = snapshot.etaDistanceMeters {
                            detailLine("地图距离", "\(Int(distance.rounded())) 米")
                        }
                        if let arrival = snapshot.etaArrivalAt {
                            detailLine("预计到达", CommuteFormat.clock(arrival, timeZone: timezone))
                        }
                        if let departure = snapshot.etaDepartureAt {
                            detailLine("预计出发", CommuteFormat.clock(departure, timeZone: timezone))
                        }
                        detailLine("查询于", CommuteFormat.clock(snapshot.queriedAt, timeZone: timezone))
                        if !snapshot.candidates.isEmpty {
                            detailLine("备选路线", "\(snapshot.candidates.count) 条（各含自身距离，不共用总 ETA）")
                        }
                        Text("地图耗时按目标到达时间查询，以 \(snapshot.provider) 快照随检查提交；服务器建议与此卡分开显示，互不替代。")
                            .font(.captionText)
                            .foregroundStyle(Palette.faint)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text("尚未取得本机路线。点“更新路线并分析”会先查地图，再请求服务器建议。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private var historicalReason: (title: String, detail: String) {
        if let advisory, let plan, advisory.planRevision != plan.revision {
            return ("旧设置的建议",
                    "这条建议来自修改前的通勤设置，对当前到达时间与路线不再适用。下方内容仅供回看。")
        }
        if model.phase == .dayPassed {
            return ("今日通勤时段已过",
                    "这条建议对应今天的到达目标，已经过了时段，不再作为现在该出发的指令。")
        }
        return ("历史建议，已过期",
                "过期后的出发时间不再有效，只用于回看当时的判断，不代表当前出行建议。")
    }

    private func staleBanner() -> some View {
        let reason = historicalReason
        return SurfaceCard(tint: Palette.amberSoft) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "clock.arrow.circlepath")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Palette.amber)
                VStack(alignment: .leading, spacing: 4) {
                    Text(reason.title)
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.amber)
                    Text(reason.detail)
                        .font(.captionText)
                        .foregroundStyle(Palette.amber.opacity(0.9))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private func statusBanner(_ text: String) -> some View {
        SurfaceCard(tint: Palette.mist) {
            HStack(spacing: 8) {
                Image(systemName: "clock")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.fogDeep)
                Text(text)
                    .font(.captionText)
                    .foregroundStyle(Palette.fogDeep)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func runtimeHintCard(_ hint: CommuteStore.RuntimeHint) -> some View {
        SurfaceCard(tint: hint.isFailure ? Palette.amberSoft : Palette.fogSoft) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: hint.isFailure ? "wifi.exclamationmark" : "waveform.path.ecg")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(hint.isFailure ? Palette.amber : Palette.fogDeep)
                VStack(alignment: .leading, spacing: 3) {
                    Text(hint.text)
                        .font(.system(.caption, weight: .semibold))
                        .foregroundStyle(hint.isFailure ? Palette.amber : Palette.fogDeep)
                        .fixedSize(horizontal: false, vertical: true)
                    if let detail = hint.detail, !detail.isEmpty {
                        Text(detail)
                            .font(.microLabel)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .accessibilityIdentifier("commute.runtimeHint")
    }

    private func planUpdatedBanner(_ text: String) -> some View {
        SurfaceCard(tint: Palette.fogSoft) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.fogDeep)
                Text(text)
                    .font(.captionText)
                    .foregroundStyle(Palette.fogDeep)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 只有后端明确处于“正在分析建议”时才说分析中；排队/未领取只说任务已保存。
    private var emptyStateTitle: String {
        guard store.processingStateText != nil else { return "还没有可展示的建议" }
        if store.checkStatus == .running, store.checkPhase == .analyzing {
            return "正在分析建议"
        }
        return "任务已保存"
    }

    private var emptyState: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text(emptyStateTitle)
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                Text(store.processingStateText
                     ?? store.errorNotice
                     ?? "保存通勤设置后，服务器会在检查时间生成建议；打开页面不会自动创建分析。")
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                if store.plan != nil {
                    PrimaryButton(title: "重新分析",
                                  enabled: !store.routeState.isFetching && !store.isRefreshing
                                    && store.pendingSubmission == nil,
                                  identifier: "commute.updateRoute") {
                        Task { await store.updateRouteAndAnalyze() }
                    }
                    SecondaryButton(title: "刷新处理状态") {
                        Task { await store.refresh() }
                    }
                } else {
                    PrimaryButton(title: "去设置通勤") {
                        state.open(.commuteSettings)
                    }
                }
            }
        }
    }

    private func detailLine(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.captionText).foregroundStyle(Palette.faint)
            Spacer()
            Text(value).font(.captionText).foregroundStyle(Palette.muted)
        }
    }

    private func metric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.microLabel).foregroundStyle(Palette.faint)
            Text(value)
                .font(.system(.title3, design: .rounded).weight(.medium))
                .foregroundStyle(Palette.ink)
        }
    }

    private func providerTitle(_ provider: String) -> String {
        switch provider {
        case "apple.weatherkit", "weatherkit": return "Apple WeatherKit"
        case "apple.maps.server", "apple_maps_server_api", "maps_server": return "Apple Maps"
        case "open_meteo", "open-meteo": return "Open-Meteo 天气"
        case "mapkit_client", "client", "mapkit": return "App 内 MapKit 快照"
        case "unavailable": return "未提供"
        case "mock": return "演示数据"
        default: return "其他来源"
        }
    }

    private func freshnessTitle(_ freshness: String) -> String {
        switch freshness {
        case "fresh": return "获取成功"
        case "stale": return "数据已过期"
        case "unavailable": return "不可用"
        default: return "状态未知"
        }
    }

    static func reasonTitle(_ code: String) -> String {
        switch code {
        case "rain_probability_above_threshold", "WEATHER_RAIN_THRESHOLD_MET": return "降雨概率高于阈值"
        case "rain_buffer_applied", "RAIN_BUFFER_APPLIED": return "已加雨天预留"
        case "buffer_applied", "BUFFER_APPLIED": return "已含基础预留"
        case "eta_available", "ROUTE_FRESH": return "已取得路线耗时"
        case "eta_unavailable", "ROUTE_UNAVAILABLE": return "缺少路线耗时"
        case "weather_unavailable", "WEATHER_UNAVAILABLE": return "缺少天气数据"
        case "target_arrival_in_past", "TARGET_ARRIVAL_IN_PAST": return "目标到达时间已过"
        default: return code
        }
    }
}
