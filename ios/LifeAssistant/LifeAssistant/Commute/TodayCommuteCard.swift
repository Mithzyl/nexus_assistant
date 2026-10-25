import SwiftUI

/// 今天页的通勤卡片：展示层只读统一的结构化建议，不再显示“数据完整”这类技术状态。
/// 整张卡进入详情；只有确实可执行时才把“开始导航”作为主操作。
struct TodayCommuteCard: View {
    @EnvironmentObject private var store: CommuteStore
    @EnvironmentObject private var state: AppState
    @State private var refreshing = false

    private var presentation: CommutePresentation {
        .make(advisory: store.advisory, plan: store.plan, syncState: store.syncState,
              demoMode: store.demoMode, isRefreshing: store.isRefreshing)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let plan = store.plan {
                configuredCard(plan)
            } else {
                setupRow
            }
            if store.plan != nil, let history = store.historicalAdvisory, history.id != store.advisory?.id {
                historyCard(history)
            }
        }
        .padding(.horizontal, Spacing.screen)
    }

    /// 历史建议只回看：降低强调、文字+图标说明，不删除记录也不改业务状态。
    private func historyCard(_ advisory: CommuteAdvisory) -> some View {
        let outdated = store.plan.map { advisory.planRevision != $0.revision } ?? false
        return Button {
            state.open(.commuteAdvisory(advisory.id))
        } label: {
            SurfaceCard(padding: 12, tint: Palette.amberSoft) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "clock.arrow.circlepath")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Palette.amber)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(outdated ? "历史建议 · 旧设置" : "历史建议，已过期")
                            .font(.system(.subheadline, weight: .semibold))
                            .foregroundStyle(Palette.amber)
                        Text("\(CommuteFormat.dayTime(advisory.generatedAt)) 生成 · 仅供回看，不代表当前出行")
                            .font(.microLabel)
                            .foregroundStyle(Palette.amber.opacity(0.85))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Palette.faint)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("commute.history")
    }

    // MARK: - 已配置

    private func configuredCard(_ plan: CommutePlan) -> some View {
        let model = presentation
        return SurfaceCard(padding: 14) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: plan.transportMode.symbol)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Palette.fog)
                    Text(titleLine(model))
                        .font(.microLabel)
                        .foregroundStyle(Palette.muted)
                    Spacer()
                    Text(model.statusText)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }

                Text(model.headline)
                    .font(.system(.title2, design: .rounded).weight(.semibold))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)

                Text(model.arrivalLine)
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)

                if !model.weatherLine.isEmpty {
                    Text(model.weatherLine)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let notice = model.weatherNotice {
                    Text(notice)
                        .font(.microLabel)
                        .foregroundStyle(Palette.amber)
                }

                if let status = store.processingStateText {
                    Label(status, systemImage: "clock")
                        .font(.microLabel)
                        .foregroundStyle(Palette.fogDeep)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let notice = store.planUpdatedNotice, model.phase == .noAdvisory {
                    Text(notice)
                        .font(.microLabel)
                        .foregroundStyle(Palette.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let aiLine = model.aiTodayLine {
                    Text(aiLine)
                        .font(.microLabel)
                        .foregroundStyle(model.aiIsHistorical ? Palette.amber : Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let weatherUpdated = model.weatherUpdatedText {
                    Text(weatherUpdated)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }
                ForEach(Array(model.aiBasis.prefix(2).enumerated()), id: \.offset) { _, basis in
                    Text(basis)
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                        .lineLimit(2)
                }

                HStack(spacing: 10) {
                    if let updated = model.updatedText {
                        Text(updated)
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                    }
                    Spacer()
                    footerAction(model)
                }
                .padding(.top, 2)
            }
            .contentShape(Rectangle())
        }
        .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .onTapGesture {
            openDetail()
        }
    }

    @ViewBuilder
    private func footerAction(_ model: CommutePresentation) -> some View {
        switch model.phase {
        case .actionable:
            if let plan = store.plan {
                inlineAction(title: "开始导航", systemImage: "arrow.triangle.turn.up.right.diamond") {
                    CommuteNavigator.open(plan: plan)
                }
            }
        case .noAdvisory:
            // 只打开详情；创建分析必须由详情页的显式操作触发。
            inlineAction(title: "查看已有结果", systemImage: "chevron.right") {
                openDetail()
            }
        case .draft:
            inlineAction(title: "完成设置", systemImage: "chevron.right") {
                state.open(.commuteSettings)
            }
        case .dayPassed, .expired:
            inlineAction(title: "查看已有结果", systemImage: "chevron.right") {
                openDetail()
            }
        }
    }

    private func inlineAction(title: String, systemImage: String, enabled: Bool = true,
                              action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(title)
                Image(systemName: systemImage)
                    .font(.system(size: 10, weight: .semibold))
            }
            .font(.system(.subheadline, weight: .semibold))
            .foregroundStyle(enabled ? Palette.fogDeep : Palette.faint)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityHint("通勤")
    }

    private func titleLine(_ model: CommutePresentation) -> String {
        model.dayLabel.isEmpty ? model.planName : "\(model.planName) · \(model.dayLabel)"
    }

    private func openDetail() {
        guard let plan = store.plan else {
            state.open(.commuteSettings)
            return
        }
        if let advisory = store.advisory {
            state.open(.commuteAdvisory(advisory.id))
        } else {
            state.open(.commuteAdvisory(""))
        }
        _ = plan
    }

    // MARK: - 未配置

    private var setupRow: some View {
        Button {
            state.open(.commuteSettings)
        } label: {
            SurfaceCard(padding: 14) {
                HStack(spacing: 12) {
                    Image(systemName: "car.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(Palette.fog)
                        .frame(width: 32, height: 32)
                        .background(Palette.mist, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("设置通勤提醒")
                            .font(.cardTitle)
                            .foregroundStyle(Palette.ink)
                        Text("固定出发地和目的地，由服务器检查天气与路况")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .multilineTextAlignment(.leading)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.faint)
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
        }
        .buttonStyle(.plain)
    }
}
