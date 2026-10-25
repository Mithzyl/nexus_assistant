import SwiftUI

struct PrepareView: View {
    @EnvironmentObject private var state: AppState

    private var doneCount: Int {
        state.activeChecklist.filter { state.checks.contains($0.0) }.count
    }

    var body: some View {
        PageShell(
            title: "明晚看电影",
            subtitle: "流浪地球 3 · 10月24日 19:30 · 3号厅 7排8座"
        ) {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("准备清单")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Text("\(state.activeChecklist.count) 项里已完成 \(doneCount) 项")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                        }
                        Spacer()
                        Text("\(doneCount)/\(state.activeChecklist.count)")
                            .font(.system(size: 22, weight: .semibold, design: .rounded))
                            .foregroundStyle(Palette.fogDeep)
                    }

                    DividerLine().padding(.vertical, 12)

                    VStack(spacing: 2) {
                        ForEach(state.activeChecklist, id: \.0) { item in
                            Button {
                                state.toggleCheck(item.0)
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: state.checks.contains(item.0) ? "checkmark.circle.fill" : "circle")
                                        .font(.system(size: 20))
                                        .foregroundStyle(state.checks.contains(item.0) ? Palette.green : Palette.faint)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.1)
                                            .font(.system(size: 15, weight: .medium))
                                            .foregroundStyle(Palette.ink)
                                            .strikethrough(state.checks.contains(item.0), color: Palette.faint)
                                        Text(item.2)
                                            .font(.microLabel)
                                            .foregroundStyle(Palette.muted)
                                    }
                                    Spacer()
                                }
                                .padding(.vertical, 10)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                if state.cinemaMemory?.enabled == false {
                    SurfaceCard(tint: Palette.amberSoft) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("影院经验已停用")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.amber)
                            Text("清单里不再出现薄外套。重新启用后，这条建议会回到清单。")
                                .font(.captionText)
                                .foregroundStyle(Palette.amber.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                Text("准备清单来自已保存的票据和你启用的经验，不会自己添加没根据的东西。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)

                SecondaryButton(title: "打开电影票") {
                    state.open(.ticket)
                }
            }
        }
    }
}

struct TicketView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        PageShell(title: "流浪地球 3", subtitle: "电影电子票 · 仅供原型体验") {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("10月24日 · 周六")
                                    .font(.microLabel)
                                    .foregroundStyle(Palette.faint)
                                Text("19:30")
                                    .font(.system(size: 30, weight: .semibold, design: .rounded))
                                    .foregroundStyle(Palette.ink)
                            }
                            Spacer()
                            TagPill(text: "3号厅", tone: .fog)
                        }

                        DividerLine()

                        HStack(spacing: 30) {
                            ticketField("影院", "万达影城柳南店")
                            ticketField("座位", "7排8座")
                        }

                        DividerLine()

                        VStack(alignment: .leading, spacing: 4) {
                            Text("取票码")
                                .font(.microLabel)
                                .foregroundStyle(Palette.faint)
                            Text("DEMO 4826 0318")
                                .font(.system(size: 20, weight: .semibold, design: .monospaced))
                                .foregroundStyle(Palette.fogDeep)
                        }
                    }
                }

                Text("这张票是本地示例，不能用于真实取票，也没有连接影院或支付。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)

                SecondaryButton(title: "查看原始依据") {
                    state.open(.source("ticket"))
                }
                PrimaryButton(title: "打开出门清单") {
                    state.open(.prepare)
                }
            }
        }
    }

    private func ticketField(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.microLabel)
                .foregroundStyle(Palette.faint)
            Text(value)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(Palette.ink)
        }
    }
}

struct WishView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        PageShell(title: "去柳江边散散步", subtitle: "10月20日留下的心愿") {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("需要")
                                .font(.captionText)
                                .foregroundStyle(Palette.faint)
                            Spacer()
                            Text("60 分钟")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(Palette.ink)
                        }
                        HStack {
                            Text("可用时间")
                                .font(.captionText)
                                .foregroundStyle(Palette.faint)
                            Spacer()
                            Text("明天 15:00—17:00")
                                .font(.system(size: 15, weight: .medium))
                                .foregroundStyle(Palette.ink)
                        }
                        DividerLine()
                        Text("这段时间是手动录入的示例，没有连接日历、天气或地图。")
                            .font(.microLabel)
                            .foregroundStyle(Palette.muted)
                    }
                }

                if state.wishScheduled {
                    SurfaceCard(tint: Palette.greenSoft) {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("已经放进安排")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.green)
                            Text("明天 15:00—17:00，留出 60 分钟。取消安排不会删掉这个心愿。")
                                .font(.captionText)
                                .foregroundStyle(Palette.green.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                PrimaryButton(title: state.wishScheduled ? "调整时间" : "为它找时间") {
                    state.open(.wishPlan)
                }
                SecondaryButton(title: "查看原始依据") {
                    state.open(.source("wish"))
                }
            }
        }
    }
}

struct WishPlanView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        PageShell(title: "安排时间", subtitle: "先给一份草稿，确认后才写进安排") {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("草稿")
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                        HStack {
                            Text("明天 15:00—17:00")
                                .font(.system(size: 18, weight: .semibold))
                                .foregroundStyle(Palette.ink)
                            Spacer()
                            TagPill(text: "60 分钟", tone: .fog)
                        }
                        Text("这段时间有 2 小时空档，放得下这个心愿。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                        DividerLine()
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark.seal")
                                .font(.system(size: 12))
                                .foregroundStyle(Palette.green)
                            Text("没有和其他安排冲突")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                }

                PrimaryButton(title: "确认加入安排") {
                    state.confirmWishSchedule()
                    dismiss()
                }
                SecondaryButton(title: "取消") {
                    dismiss()
                }
            }
        }
    }
}

struct ProjectView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        PageShell(title: "周末短途旅行准备", subtitle: "3 步 · 酒店已选，交通未定") {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Text("进度")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Spacer()
                            Text("\(state.projectProgress)/3")
                                .font(.system(size: 18, weight: .semibold, design: .rounded))
                                .foregroundStyle(Palette.fogDeep)
                        }
                        HStack(spacing: 6) {
                            ForEach(0..<3) { index in
                                Capsule()
                                    .fill(index < state.projectProgress ? Palette.fog : Palette.line)
                                    .frame(height: 5)
                            }
                        }
                    }
                }

                stepCard(
                    index: 1,
                    title: "决定住哪里",
                    detail: "江景客栈 · 已选好，但没有代订",
                    done: true
                )

                stepCard(
                    index: 2,
                    title: "确认交通",
                    detail: state.transportConfirmed ? "已确认出发方式" : "还没决定坐什么、几点出发",
                    done: state.transportConfirmed
                ) {
                    state.confirmTransport()
                }

                stepCard(
                    index: 3,
                    title: "整理行李",
                    detail: state.packingStarted ? "行李清单已开始" : "等交通确定后再收拾",
                    done: state.packingStarted
                ) {
                    state.startPacking()
                }

                Text("助手只按笔记里的顺序推进，不会替你订酒店或买票。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)

                SecondaryButton(title: "查看原始依据") {
                    state.open(.source("project"))
                }
            }
        }
    }

    @ViewBuilder
    private func stepCard(index: Int, title: String, detail: String, done: Bool, action: (() -> Void)? = nil) -> some View {
        SurfaceCard(padding: 14) {
            HStack(alignment: .top, spacing: 12) {
                Text("\(index)")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(done ? .white : Palette.fogDeep)
                    .frame(width: 24, height: 24)
                    .background(done ? Palette.fog : Palette.mist)
                    .clipShape(Circle())
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(done ? Palette.muted : Palette.ink)
                    Text(detail)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                if done {
                    Image(systemName: "checkmark")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.green)
                } else if let action {
                    Button("完成", action: action)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.fogDeep)
                        .buttonStyle(.plain)
                }
            }
        }
    }
}

struct TravelView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        PageShell(title: "行程改期影响", subtitle: "到达从 11:00 改到 14:00") {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            column("原到达", "11:00", struck: state.travelAccepted)
                            Spacer()
                            Image(systemName: "arrow.right")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(Palette.faint)
                            Spacer()
                            column("新到达", "14:00", struck: false)
                        }
                        DividerLine()
                        VStack(alignment: .leading, spacing: 8) {
                            impactRow("接站安排 11:30", "晚于新到达时间，要重新约")
                            impactRow("活动开始 13:00", "到达前已开始，需要说明")
                        }
                    }
                }

                if state.travelAccepted {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("待联系")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            ForEach(["接站师傅", "活动联系人"], id: \.self) { name in
                                Button {
                                    state.toggleContact(name)
                                } label: {
                                    HStack(spacing: 10) {
                                        Image(systemName: state.contacts.contains(name) ? "checkmark.circle.fill" : "circle")
                                            .foregroundStyle(state.contacts.contains(name) ? Palette.green : Palette.faint)
                                        Text(name)
                                            .font(.system(size: 15))
                                            .foregroundStyle(Palette.ink)
                                        Spacer()
                                    }
                                    .padding(.vertical, 8)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                            DividerLine()
                            Text("没有代发消息，也没有办理改签。勾选只是你自己的备忘。")
                                .font(.microLabel)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                } else {
                    PrimaryButton(title: "采纳改期，生成待办") {
                        state.acceptTravelChange()
                    }
                    Text("采纳只影响这份准备清单，不会向任何人发消息。")
                        .font(.captionText)
                        .foregroundStyle(Palette.faint)
                }

                SecondaryButton(title: "查看原始依据") {
                    state.open(.source("travel"))
                }
            }
        }
    }

    private func column(_ label: String, _ value: String, struck: Bool) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.microLabel)
                .foregroundStyle(Palette.faint)
            Text(value)
                .font(.system(size: 24, weight: .semibold, design: .rounded))
                .foregroundStyle(struck ? Palette.faint : Palette.ink)
                .strikethrough(struck)
        }
    }

    private func impactRow(_ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(Palette.amber)
                .frame(width: 5, height: 5)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Palette.ink)
                Text(detail)
                    .font(.microLabel)
                    .foregroundStyle(Palette.muted)
            }
            Spacer()
        }
    }
}

struct AddedView: View {
    @EnvironmentObject private var state: AppState
    let matterId: String

    private var matter: Matter? { state.matter(matterId) }

    var body: some View {
        PageShell(
            title: matter?.title ?? "已收下",
            subtitle: matter?.subtitle ?? "",
            trailing: matter?.amount
        ) {
            VStack(alignment: .leading, spacing: 16) {
                if let matter {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("整理结果")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            ForEach(matter.detail) { row in
                                HStack {
                                    Text(row.label)
                                        .font(.captionText)
                                        .foregroundStyle(Palette.faint)
                                    Spacer()
                                    Text(row.value)
                                        .font(.system(size: 14, weight: .medium))
                                        .foregroundStyle(Palette.ink)
                                        .multilineTextAlignment(.trailing)
                                }
                            }
                        }
                    }

                    if matter.kind == .unknown {
                        SurfaceCard(tint: Palette.amberSoft) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text("还没有替你分类")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundStyle(Palette.amber)
                                Text("这句话没有足够的金额、日期或对象可以判断。它先留在这里，等你决定它属于哪件事。")
                                    .font(.captionText)
                                    .foregroundStyle(Palette.amber.opacity(0.9))
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }

                    if let evidenceId = matter.evidenceId {
                        SecondaryButton(title: "查看原始依据") {
                            state.open(.source(evidenceId))
                        }
                    }
                } else {
                    Text("这件事已经不在了。")
                        .font(.bodyText)
                        .foregroundStyle(Palette.muted)
                }
            }
        }
    }
}
