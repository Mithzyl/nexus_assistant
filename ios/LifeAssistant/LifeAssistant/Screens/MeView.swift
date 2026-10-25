import SwiftUI

struct MeView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var session: PASessionStore
    @EnvironmentObject private var asks: AskStore
    @State private var showLogin = false
    @State private var showLogoutConfirm = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                ScreenHeader(
                    title: "我的",
                    subtitle: "入口、记忆与隐私都在这里",
                    onCapture: { state.openCapture() }
                )

                accountCard
                    .padding(.horizontal, Spacing.screen)

                group(title: "系统入口") {
                    RowLink(icon: "switch.2", title: "控制中心 · 收下这个", detail: "把选中的内容直接交给助手", status: "未连接") {
                        state.open(.setup)
                    }
                    DividerLine()
                    RowLink(icon: "hand.tap", title: "操作按钮 · 快捷指令", detail: "锁屏和快捷指令里的同一个动作", status: "未连接") {
                        state.open(.setup)
                    }
                }
                .padding(.horizontal, Spacing.screen)

                group(title: "通勤与天气") {
                    RowLink(icon: "car", title: "通勤设置", detail: "固定地点与时间，由服务器检查天气和路况", status: nil) {
                        state.open(.commuteSettings)
                    }
                    DividerLine()
                    RowLink(icon: "bell", title: "通知设置", detail: "系统权限与 App 开关分开管理", status: nil) {
                        state.open(.commuteNotifications)
                    }
                }
                .padding(.horizontal, Spacing.screen)

                group(title: "你的内容") {
                    RowLink(icon: "tray.full", title: "数据来源", detail: "日历、提醒事项与备忘录导入", status: nil) {
                        state.open(.dataSources)
                    }
                    .accessibilityIdentifier("me.dataSources")
                    DividerLine()
                    RowLink(icon: "brain", title: "个人记忆", detail: "\(state.memories.filter(\.enabled).count) 条生效 · \(state.memories.count) 条保存", status: nil) {
                        state.open(.memories)
                    }
                    DividerLine()
                    RowLink(icon: "square.and.pencil", title: "记住一点经验", detail: "主动写下的，不靠模型推测", status: nil) {
                        state.open(.lesson)
                    }
                }
                .padding(.horizontal, Spacing.screen)

                group(title: "内容与隐私") {
                    RowLink(
                        icon: "lock",
                        title: "原文保留",
                        detail: state.preferences.keepOriginalText ? "保留 \(state.preferences.retainDays) 天，可随时关闭" : "不保留原文，只留结构化字段",
                        status: nil
                    ) {
                        state.open(.privacy)
                    }
                    DividerLine()
                    RowLink(icon: "info.circle", title: "关于这个示例", detail: "数据都在本机内存，退出即还原", status: nil) {
                        state.open(.info)
                    }
                }
                .padding(.horizontal, Spacing.screen)

                Text(session.isSignedIn
                     ? "已连接 PA 服务器。控制中心、快捷指令等系统入口仍未接入。"
                     : "未登录 PA 服务器。问答与通勤提醒需要登录后才由服务器处理。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .padding(.horizontal, Spacing.screen)
                    .padding(.top, 4)
            }
            .padding(.bottom, 28)
        }
        .scrollIndicators(.hidden)
        .background(Palette.warm)
        .toolbar(.hidden, for: .navigationBar)
        .sheet(isPresented: $showLogin) {
            PALoginSheet()
                .environmentObject(session)
                .environmentObject(asks)
        }
        .confirmationDialog("退出登录？", isPresented: $showLogoutConfirm, titleVisibility: .visible) {
            Button("退出登录", role: .destructive) {
                Task {
                    await session.logout()
                    asks.clearForLogout()
                }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("本机的问答与通勤会话会被清理；服务器上的账号数据仍保留。")
        }
    }

    @ViewBuilder
    private var accountCard: some View {
        SurfaceCard {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: session.isSignedIn ? "person.crop.circle.fill" : "person.crop.circle")
                    .font(.system(size: 40))
                    .foregroundStyle(Palette.fog)
                VStack(alignment: .leading, spacing: 4) {
                    Text(accountTitle)
                        .font(.cardTitle)
                        .foregroundStyle(Palette.ink)
                        .accessibilityIdentifier("account.title")
                    Text(accountDetail)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)

                    switch session.state {
                    case .signedIn(let me), .offline(let me):
                        Text(me.email)
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                    default:
                        EmptyView()
                    }

                    if session.isSignedIn {
                        sessionDetail
                    }

                    if let notice = session.lastError, session.state == .signedOut {
                        Text(notice)
                            .font(.captionText)
                            .foregroundStyle(Palette.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    HStack(spacing: 12) {
                        switch session.state {
                        case .signedOut, .expired:
                            Button("登录") { showLogin = true }
                                .font(.system(.subheadline, weight: .semibold))
                                .foregroundStyle(Palette.fogDeep)
                                .accessibilityIdentifier("account.login")
                        case .unverified:
                            Button("验证会话") { Task { await session.bootstrap() } }
                                .font(.system(.subheadline, weight: .semibold))
                                .foregroundStyle(Palette.fogDeep)
                            Button("重新登录") { showLogin = true }
                                .font(.subheadline)
                                .foregroundStyle(Palette.fogDeep)
                        case .loading:
                            ProgressView()
                        default:
                            Button("退出登录") { showLogoutConfirm = true }
                                .font(.system(.subheadline, weight: .semibold))
                                .foregroundStyle(Palette.fogDeep)
                                .accessibilityIdentifier("account.logout")
                        }
                    }
                }
                Spacer()
            }
        }
    }

    /// 会话期限只能来自服务端；没有续期接口时如实说明，不展示虚构的延长。
    @ViewBuilder
    private var sessionDetail: some View {
        if let expiresAt = session.expiresAt {
            Text("会话有效期至 \(CommuteFormat.dayTime(expiresAt))")
                .font(.microLabel)
                .foregroundStyle(Palette.faint)
                .accessibilityIdentifier("account.expiry")
        }
        switch session.renewalState {
        case .idle:
            EmptyView()
        case .renewing:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("正在向服务器续期…")
                    .font(.microLabel)
                    .foregroundStyle(Palette.muted)
            }
        case .renewed(let date):
            Text("已续期至 \(CommuteFormat.dayTime(date))")
                .font(.microLabel)
                .foregroundStyle(Palette.muted)
                .accessibilityIdentifier("account.renewed")
        case .unsupported(let message), .failed(let message):
            Text(message)
                .font(.microLabel)
                .foregroundStyle(Palette.amber)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("account.renewState")
            Button("重试续期") {
                Task { await session.renew(force: true) }
            }
            .font(.microLabel)
            .foregroundStyle(Palette.fogDeep)
            .accessibilityIdentifier("account.renew")
        }
    }

    private var accountTitle: String {
        switch session.state {
        case .loading: return "正在验证会话…"
        case .signedOut: return "未登录"
        case .expired: return "会话已过期"
        case .unverified: return "登录状态尚未验证"
        case .signedIn(let me): return me.displayName
        case .offline(let me): return me.displayName + "（离线）"
        }
    }

    private var accountDetail: String {
        switch session.state {
        case .loading:
            return "正在向 PA 服务器确认登录状态。"
        case .signedOut:
            return "登录 PA 本地账号后，问答与通勤提醒由服务器处理。"
        case .expired:
            return "登录状态已失效，请重新登录。"
        case .unverified:
            return "已保存登录状态，但暂时无法验证；网络恢复后可重试。"
        case .signedIn:
            return "已登录 PA 服务器。"
        case .offline:
            return "网络不可用，保留上次登录身份与本地记录。"
        }
    }

    private func group<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionHeading(title: title)
            VStack(spacing: 0) {
                content()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.card)
            .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 20, style: .continuous)
                    .strokeBorder(Palette.line, lineWidth: 1)
            )
        }
    }
}
