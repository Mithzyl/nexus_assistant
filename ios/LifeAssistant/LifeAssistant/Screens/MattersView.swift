import SwiftUI

struct MattersView: View {
    @EnvironmentObject private var state: AppState
    @State private var filter = "进行中"

    private let filters = ["进行中", "已完成", "全部"]

    private var visible: [Matter] {
        switch filter {
        case "已完成": return state.matters.filter { $0.status == .done }
        case "全部": return state.matters
        default: return state.matters.filter { $0.status == .active }
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                ScreenHeader(
                    title: "事务",
                    subtitle: "同一件事，汇集订单、通知和下一步",
                    onCapture: { state.openCapture() }
                )

                HStack(spacing: 8) {
                    ForEach(filters, id: \.self) { item in
                        Button {
                            filter = item
                        } label: {
                            Text(item)
                                .font(.system(size: 14, weight: filter == item ? .semibold : .regular))
                                .foregroundStyle(filter == item ? Color.white : Palette.muted)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 8)
                                .background(filter == item ? Palette.accentFill : Palette.card)
                                .clipShape(Capsule())
                                .overlay(Capsule().strokeBorder(Palette.line, lineWidth: filter == item ? 0 : 1))
                                .frame(minHeight: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    Spacer()
                }
                .padding(.horizontal, Spacing.screen)

                if visible.isEmpty {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("这里还没有已完成的事")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Text("处理完的退款、看过的电影会出现在这里。")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                    .padding(.horizontal, Spacing.screen)
                } else {
                    VStack(spacing: 10) {
                        ForEach(visible) { matter in
                            matterCard(matter)
                        }
                    }
                    .padding(.horizontal, Spacing.screen)
                }
            }
            .padding(.bottom, 28)
        }
        .scrollIndicators(.hidden)
        .background(Palette.warm)
        .toolbar(.hidden, for: .navigationBar)
    }

    private func matterCard(_ matter: Matter) -> some View {
        Button {
            state.open(matter.route)
        } label: {
            SurfaceCard {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: matter.kind.icon)
                            .font(.system(size: 15))
                            .foregroundStyle(Palette.fog)
                            .frame(width: 30, height: 30)
                            .background(Palette.mist)
                            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

                        VStack(alignment: .leading, spacing: 3) {
                            Text(matter.title)
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Text(matter.subtitle)
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                                .multilineTextAlignment(.leading)
                        }
                        Spacer(minLength: 8)
                        if let amount = matter.amount {
                            Text(amount)
                                .font(.system(size: 15, weight: .semibold, design: .rounded))
                                .foregroundStyle(Palette.ink)
                        }
                    }

                    HStack(spacing: 8) {
                        TagPill(text: matter.kind.label, tone: .fog)
                        if let stage = matter.refundStage {
                            TagPill(text: stage.label, tone: stage == .received ? .green : .amber)
                        }
                        Spacer()
                        Text(matter.dateText)
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                    }

                    if let next = matter.nextStep {
                        DividerLine()
                        HStack(spacing: 6) {
                            Image(systemName: "arrow.turn.down.right")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Palette.fog)
                            Text(next)
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Palette.faint)
                        }
                    }
                }
            }
        }
        .buttonStyle(.plain)
    }
}
