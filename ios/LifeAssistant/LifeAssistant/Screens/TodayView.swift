import SwiftUI

/// 首页只回答两件事：哪里需要你决定、助手刚整理了什么。
/// 入口说明、统计数字不再占首屏。
struct TodayView: View {
    @EnvironmentObject private var state: AppState

    private var activeCount: Int {
        state.matters.filter { $0.status == .active }.count
    }

    private var recentLogs: [ActivityLog] {
        Array(state.logs.prefix(4))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Spacing.group) {
                header
                quickActions
                TodayCommuteCard()

                if state.pendingAmbiguity {
                    pendingCard
                }

                recentSection
                if state.isDemo {
                    upcomingSection
                    Text("演示模式 · 本地示例数据")
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 4)
                }
            }
            .padding(.top, 6)
            .padding(.bottom, Spacing.section)
        }
        .scrollIndicators(.hidden)
        .background(Palette.warm)
        .toolbar(.hidden, for: .navigationBar)
    }

    // MARK: - 1. 标题与日期

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("今天")
                .font(.screenTitle)
                .foregroundStyle(Palette.ink)
            Text(SampleData.today)
                .font(.captionText)
                .foregroundStyle(Palette.muted)
        }
        .padding(.horizontal, Spacing.screen)
    }

    // MARK: - 2. 紧凑快捷操作行

    private var quickActions: some View {
        HStack(spacing: 10) {
            Button {
                state.openCapture()
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: "square.and.arrow.down")
                    Text("收下内容")
                }
                .font(.system(.subheadline, weight: .semibold))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 52)
                .background(Palette.accentFill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityHint("打开内容来源，选择这次要交来的示例")

            Button {
                state.openCapture(say: true)
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: "text.bubble")
                    Text("记一句")
                }
                .font(.system(.subheadline, weight: .medium))
                .foregroundStyle(Palette.fogDeep)
                .frame(maxWidth: .infinity, minHeight: 52)
                .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .buttonStyle(.plain)
            .chromeGlass(in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .padding(.horizontal, Spacing.screen)
    }

    // MARK: - 3. 待确认重点卡

    private var pendingCard: some View {
        Button {
            state.open(.ambiguity)
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                Text("待你确认 · 1")
                    .font(.microLabel)
                    .foregroundStyle(Palette.amber)

                Text("这笔 ¥129 退款属于哪件商品？")
                    .font(.headline)
                    .foregroundStyle(Palette.ink)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)

                Text("咖啡机和收纳架金额相同，通知缺少订单号。")
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 4) {
                    Text("确认归属")
                        .font(.system(.subheadline, weight: .semibold))
                    Image(systemName: "chevron.right")
                        .font(.system(.caption, weight: .semibold))
                }
                .foregroundStyle(Palette.amber)
                .padding(.top, 4)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.amberSoft, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, Spacing.screen)
    }

    // MARK: - 4. 最近整理

    private var recentSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeading(title: "最近整理")

            if recentLogs.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    Text("还没有整理记录")
                        .font(.system(.subheadline, weight: .medium))
                        .foregroundStyle(Palette.ink)
                    Text("交入第一份内容后，结果会出现在这里。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Palette.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(recentLogs.enumerated()), id: \.element.id) { index, log in
                        Button {
                            if let target = log.target {
                                state.open(target)
                            }
                        } label: {
                            recentRow(log)
                        }
                        .buttonStyle(.plain)
                        .disabled(log.target == nil)

                        if index < recentLogs.count - 1 {
                            DividerLine()
                                .padding(.leading, 58)
                        }
                    }
                }
                .background(Palette.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(Palette.line.opacity(0.6), lineWidth: 0.5)
                )
            }
        }
        .padding(.horizontal, Spacing.screen)
    }

    private func recentRow(_ log: ActivityLog) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon(for: log.target))
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(Palette.fog)
                .frame(width: 32, height: 32)
                .background(Palette.mist, in: RoundedRectangle(cornerRadius: 9, style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                Text(log.title)
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Text(log.detail)
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.faint)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(minHeight: 56)
        .contentShape(Rectangle())
    }

    private func icon(for route: Route?) -> String {
        guard let route else { return "checkmark" }
        switch route {
        case .refund, .shelf: return "arrow.uturn.backward"
        case .prepare, .ticket: return "ticket"
        case .wish, .wishPlan: return "sparkles"
        case .project: return "checklist"
        case .travel: return "airplane"
        case .memories, .memory: return "brain"
        case .ambiguity: return "questionmark"
        default: return "checkmark"
        }
    }

    // MARK: - 5. 接下来用得上 + 进行中事务入口

    private var upcomingSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionHeading(title: "接下来用得上")

            VStack(spacing: 0) {
                Button {
                    state.open(.prepare)
                } label: {
                    upcomingRow(
                        icon: "ticket",
                        title: "明晚看电影",
                        detail: cinemaHint,
                        status: "19:30"
                    )
                }
                .buttonStyle(.plain)

                DividerLine()
                    .padding(.leading, 58)

                Button {
                    state.select(.matters)
                } label: {
                    upcomingRow(
                        icon: "square.stack.3d.up",
                        title: "进行中事务",
                        detail: "\(activeCount) 件 · 订单、退款与安排都在这里",
                        status: nil
                    )
                }
                .buttonStyle(.plain)
            }
            .background(Palette.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Palette.line.opacity(0.6), lineWidth: 0.5)
            )
        }
        .padding(.horizontal, Spacing.screen)
    }

    private func upcomingRow(icon: String, title: String, detail: String, status: String?) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(Palette.fog)
                .frame(width: 32, height: 32)
                .background(Palette.mist, in: RoundedRectangle(cornerRadius: 9, style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                Text(detail)
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 8)

            if let status {
                Text(status)
                    .font(.microLabel)
                    .foregroundStyle(Palette.faint)
            }

            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.faint)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .frame(minHeight: 56)
        .contentShape(Rectangle())
    }

    private var cinemaHint: String {
        if state.cinemaMemory?.enabled == true {
            return "出门清单 · 影院经验：带薄外套"
        }
        return "出门清单 · 影院经验已停用"
    }
}
