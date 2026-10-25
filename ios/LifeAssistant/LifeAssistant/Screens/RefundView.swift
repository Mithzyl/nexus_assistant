import SwiftUI

struct RefundView: View {
    @EnvironmentObject private var state: AppState

    private var matter: Matter? { state.coffee }
    private var stage: RefundStage { state.coffeeStage }

    var body: some View {
        PageShell(
            title: "便携咖啡机",
            subtitle: "晨间生活店 · 订单 DEMO-20261015-129",
            trailing: matter?.amount
        ) {
            VStack(alignment: .leading, spacing: 16) {
                timeline

                if stage == .merchantInitiated {
                    SurfaceCard(tint: Palette.amberSoft) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("商家已发起，不等于已到账")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.amber)
                            Text("这条通知是进度，不是到账凭证。等到账回执或银行记录出现，才会更新成“已到账”。")
                                .font(.captionText)
                                .foregroundStyle(Palette.amber.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("这件事")
                            .font(.cardTitle)
                            .foregroundStyle(Palette.ink)
                        ForEach(matter?.detail ?? []) { row in
                            HStack {
                                Text(row.label)
                                    .font(.captionText)
                                    .foregroundStyle(Palette.faint)
                                Spacer()
                                Text(row.value)
                                    .font(.system(size: 14, weight: .medium))
                                    .foregroundStyle(Palette.ink)
                            }
                        }
                        DividerLine()
                        Button {
                            state.open(.source("order"))
                        } label: {
                            HStack(spacing: 6) {
                                Image(systemName: "doc.text")
                                Text("查看原始依据")
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 10, weight: .semibold))
                            }
                            .font(.captionText)
                            .foregroundStyle(Palette.fogDeep)
                        }
                        .buttonStyle(.plain)
                    }
                }

                if stage == .received {
                    SurfaceCard(tint: Palette.greenSoft) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("已到账")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.green)
                            Text("10月23日的回执显示 ¥129.00 已退回原支付账户。回执同时留在依据里。")
                                .font(.captionText)
                                .foregroundStyle(Palette.green.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                if stage == .received && !state.receiptUndone {
                    SecondaryButton(title: "撤销这次到账更新") {
                        state.undoReceipt()
                    }
                }

                if state.receiptUndone {
                    SurfaceCard(tint: Palette.amberSoft) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("这次到账已撤销")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.amber)
                            Text("回执还在，但不再算作已到账。同一份回执再次交入，也不会自动重新应用。")
                                .font(.captionText)
                                .foregroundStyle(Palette.amber.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    PrimaryButton(title: "重新应用这份回执") {
                        state.reapplyReceipt()
                    }
                }

                Text("状态只前进。较早的进度通知晚到时，只会补进依据，不会把“已到账”退回去。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var timeline: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("退款进度")
                        .font(.cardTitle)
                        .foregroundStyle(Palette.ink)
                    Spacer()
                    TagPill(text: stage.label, tone: stage == .received ? .green : .amber)
                }

                VStack(spacing: 0) {
                    ForEach(Array(RefundStage.allCases.enumerated()), id: \.offset) { index, item in
                        HStack(alignment: .top, spacing: 10) {
                            VStack(spacing: 0) {
                                Circle()
                                    .fill(item.index <= stage.index ? Palette.fog : Palette.line)
                                    .frame(width: 10, height: 10)
                                    .overlay(
                                        Circle()
                                            .strokeBorder(item.index == stage.index ? Palette.fog : Color.clear, lineWidth: 3)
                                            .frame(width: 16, height: 16)
                                    )
                                if index < 2 {
                                    Rectangle()
                                        .fill(item.index < stage.index ? Palette.fog : Palette.line)
                                        .frame(width: 2, height: 34)
                                }
                            }
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.label)
                                    .font(.system(size: 14, weight: item.index == stage.index ? .semibold : .regular))
                                    .foregroundStyle(item.index <= stage.index ? Palette.ink : Palette.faint)
                                Text(item.detail)
                                    .font(.microLabel)
                                    .foregroundStyle(Palette.muted)
                            }
                            Spacer()
                        }
                    }
                }
            }
        }
    }
}

/// 09｜收纳架退款：证明同样 ¥129 不等于同一件事。
/// 有独立订单、独立通知关联，但没有第二套到账回执模拟。
struct ShelfView: View {
    @EnvironmentObject private var state: AppState

    private var matter: Matter? { state.shelf }

    var body: some View {
        PageShell(
            title: "收纳架退款",
            subtitle: "家居小店 · 订单 DEMO-SHELF-1020",
            trailing: matter?.amount
        ) {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text("退款进度")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Spacer()
                            TagPill(text: state.shelfInProgress ? "通知已关联" : "待跟进", tone: .amber)
                        }
                        Text(state.shelfInProgress ? "通知已关联，等待到账" : "等待退款后续")
                            .font(.system(size: 20, weight: .semibold))
                            .foregroundStyle(Palette.ink)
                        Text(state.shelfInProgress
                             ? "这条 ¥129 通知由你确认关联。它没有订单号和到账凭据，所以还只是进度，不是到账。"
                             : "10月20日已提交退款申请，目前没有任何可以匹配的到账回执。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                SurfaceCard(tint: Palette.mist) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("原订单")
                            .font(.microLabel)
                            .foregroundStyle(Palette.fogDeep)
                        Text("收纳架 ¥129。家居小店，订单 DEMO-SHELF-1020。10月20日申请退款。")
                            .font(.bodyText)
                            .foregroundStyle(Palette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("当前没有可匹配的到账回执。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                }

                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("这件事")
                            .font(.cardTitle)
                            .foregroundStyle(Palette.ink)
                        ForEach(matter?.detail ?? []) { row in
                            HStack {
                                Text(row.label)
                                    .font(.captionText)
                                    .foregroundStyle(Palette.faint)
                                Spacer()
                                Text(row.value)
                                    .font(.system(size: 14, weight: .medium))
                                    .foregroundStyle(Palette.ink)
                            }
                        }
                    }
                }

                if state.shelfInProgress {
                    SurfaceCard {
                        Button {
                            state.open(.captureSource("ambiguous"))
                        } label: {
                            HStack(spacing: 6) {
                                Image(systemName: "doc.text")
                                Text("查看你确认关联的通知")
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 10, weight: .semibold))
                            }
                            .font(.captionText)
                            .foregroundStyle(Palette.fogDeep)
                        }
                        .buttonStyle(.plain)
                    }
                }

                Text("即使与咖啡机的金额一样，也不合并事务、不推断到账。咖啡机的回执到账或撤销，都不会改变这里。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct AmbiguityView: View {
    @EnvironmentObject private var state: AppState
    @State private var choice: String? = nil

    var body: some View {
        PageShell(
            title: "这笔 ¥129，是哪件商品？",
            subtitle: "通知里没有订单号，只能由你确认一次"
        ) {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard(tint: Palette.mist) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("你交来的通知")
                            .font(.microLabel)
                            .foregroundStyle(Palette.fogDeep)
                        Text("您有一笔 ¥129.00 退款正在处理。本条通知未包含商家名称、订单号或到账凭据。")
                            .font(.bodyText)
                            .foregroundStyle(Palette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                VStack(spacing: 10) {
                    candidate(
                        id: "coffee",
                        title: "便携咖啡机",
                        detail: "晨间生活店 · 申请退款 ¥129.00 · 10月17日"
                    )
                    candidate(
                        id: "shelf",
                        title: "收纳架",
                        detail: "家居小店 · 申请退款 ¥129.00 · 10月20日"
                    )
                }

                Text("金额相同不能自动关联。确认只把这条通知归到你选的事，不会把它标记成已到账。")
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)

                if state.ambiguityResolved == nil {
                    PrimaryButton(title: "确认归到这件", enabled: choice != nil) {
                        guard let choice else { return }
                        state.resolveAmbiguity(choice)
                    }
                    SecondaryButton(title: "稍后再说") {
                        state.path.removeLast()
                    }
                } else {
                    SurfaceCard(tint: Palette.greenSoft) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("已经归到\((state.ambiguityResolved == "coffee") ? "便携咖啡机" : "收纳架")")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(Palette.green)
                            Text("退款状态没有变化。要等明确的到账凭据，才会更新成“已到账”。")
                                .font(.captionText)
                                .foregroundStyle(Palette.green.opacity(0.9))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    PrimaryButton(title: "查看这件事务") {
                        state.open(state.ambiguityResolved == "shelf" ? .shelf : .refund)
                    }
                }
            }
        }
    }

    private func candidate(id: String, title: String, detail: String) -> some View {
        Button {
            choice = id
        } label: {
            SurfaceCard(padding: 14) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: choice == id ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(choice == id ? Palette.fog : Palette.faint)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title)
                            .font(.cardTitle)
                            .foregroundStyle(Palette.ink)
                        Text(detail)
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                    Spacer()
                    Text("¥129.00")
                        .font(.system(size: 14, weight: .semibold, design: .rounded))
                        .foregroundStyle(Palette.ink)
                }
            }
        }
        .buttonStyle(.plain)
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .strokeBorder(choice == id ? Palette.fog : Color.clear, lineWidth: 1.5)
        )
    }
}
