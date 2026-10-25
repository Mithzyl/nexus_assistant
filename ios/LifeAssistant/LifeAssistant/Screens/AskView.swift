import SwiftUI
import UIKit

struct AskView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var session: PASessionStore
    @EnvironmentObject private var asks: AskStore

    @State private var question = ""
    @State private var showLogin = false
    @State private var copiedID: String?
    @FocusState private var focused: Bool

    private static let bottomAnchor = "ask.bottom"

    private let suggestions = [
        "咖啡机退款到哪一步了？",
        "明晚看电影要带什么？",
        "柳江边散步安排在哪天？",
        "旅行准备到哪一步了？"
    ]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: Spacing.section) {
                    ScreenHeader(
                        title: "助手",
                        subtitle: "连接 PA 服务器 · 每次提问独立处理",
                        onCapture: { state.openCapture() }
                    )

                    sessionBanner
                        .padding(.horizontal, Spacing.screen)

                    if asks.items.isEmpty {
                        intro
                            .padding(.horizontal, Spacing.screen)
                    } else {
                        // 聊天按时间从上往下读：最早的问题在上，最新的问答贴着输入框。
                        VStack(alignment: .leading, spacing: 16) {
                            ForEach(asks.timeline) { item in
                                askCard(item)
                            }
                        }
                        .padding(.horizontal, Spacing.screen)
                    }

                    boundaryNote
                        .padding(.horizontal, Spacing.screen)

                    // 底部锚点：进入页面或列表变化后定位到最后一条。
                    Color.clear
                        .frame(height: 1)
                        .id(Self.bottomAnchor)
                }
                .padding(.bottom, 24)
            }
            .defaultScrollAnchor(.bottom)
            .scrollIndicators(.hidden)
            .background(Palette.warm)
            .toolbar(.hidden, for: .navigationBar)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                composer
            }
            .task {
                await asks.bindAccount()
                asks.resumePolling()
                scrollToBottom(proxy, animated: false)
            }
            .onDisappear {
                asks.pausePolling()
            }
            .onChange(of: session.state) { _, _ in
                Task { await asks.bindAccount() }
            }
            .onChange(of: asks.items.count) { _, _ in
                scrollToBottom(proxy, animated: true)
            }
            .onChange(of: asks.timeline.last?.phase) { _, _ in
                scrollToBottom(proxy, animated: true)
            }
            .sheet(isPresented: $showLogin) {
                PALoginSheet()
                    .environmentObject(session)
                    .environmentObject(asks)
            }
        }
    }

    /// 新内容插入后，同一次布局周期内高度还没确定；等下一轮 RunLoop 再滚动。
    private func scrollToBottom(_ proxy: ScrollViewProxy, animated: Bool) {
        DispatchQueue.main.async {
            if animated {
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                }
            } else {
                proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
            }
        }
    }

    // MARK: - 会话提示

    @ViewBuilder
    private var sessionBanner: some View {
        if asks.demoMode || session.demoMode {
            Text("演示数据：当前构建使用本地示例，不代表服务器已生成回答。")
                .font(.captionText)
                .foregroundStyle(Palette.amber)
        }
        switch session.state {
        case .signedIn(let me):
            HStack(spacing: 6) {
                Image(systemName: "checkmark.icloud")
                Text("已登录 \(me.displayName) · 回答来自 PA 服务器")
            }
            .font(.captionText)
            .foregroundStyle(Palette.muted)
        case .offline(let me):
            HStack(spacing: 6) {
                Image(systemName: "wifi.slash")
                Text("离线：仍显示 \(me.displayName) 的本地记录，暂不能发送")
                Button("重试") { Task { await session.bootstrap() } }
                    .font(.captionText)
            }
            .foregroundStyle(Palette.amber)
        case .unverified:
            HStack(spacing: 6) {
                Text("登录状态尚未验证。")
                Button("重试") { Task { await session.bootstrap() } }
            }
            .font(.captionText)
            .foregroundStyle(Palette.amber)
        case .expired:
            HStack(spacing: 6) {
                Text("会话已过期，请重新登录。")
                Button("登录") { showLogin = true }
            }
            .font(.captionText)
            .foregroundStyle(Palette.red)
        case .signedOut, .loading:
            SurfaceCard(tint: Palette.amberSoft) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("登录后才能向服务器提问")
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.amber)
                    Text("未登录时仍可浏览本机已有内容；输入会保留。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                    Button("登录 PA 账号") { showLogin = true }
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.fogDeep)
                }
            }
        }
    }

    // MARK: - 空态

    private var intro: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 12) {
                Text("问一句，交给服务器处理。")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Text("提问会提交到 PA 后端，由服务器等待并保存最终回答。这里不会伪造打字机效果，也不会连接上游服务。")
                    .font(.bodyText)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(spacing: 8) {
                    ForEach(suggestions, id: \.self) { item in
                        Button {
                            send(item)
                        } label: {
                            HStack(spacing: 8) {
                                Text(item)
                                    .font(.system(size: 14))
                                    .foregroundStyle(Palette.fogDeep)
                                    .multilineTextAlignment(.leading)
                                Spacer()
                                Image(systemName: "arrow.up.right")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(Palette.faint)
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 11)
                            .background(Palette.mist.opacity(0.65))
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    // MARK: - 单条问答

    private func askCard(_ item: AskItem) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Spacer()
                Text(item.question)
                    .font(.bodyText)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Palette.accentFill)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .frame(maxWidth: 280, alignment: .trailing)
                    .textSelection(.enabled)
            }

            statusCard(item)
        }
    }

    @ViewBuilder
    private func statusCard(_ item: AskItem) -> some View {
        switch item.phase {
        case .submitting:
            SurfaceCard {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("正在提交给服务器…")
                        .font(.bodyText)
                        .foregroundStyle(Palette.muted)
                }
            }
        case .submissionUncertain:
            SurfaceCard(tint: Palette.amberSoft) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("提交结果暂不确定")
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.amber)
                    Text("请求可能已经到达服务器，也可能没有。重试会复用同一个请求键，不会创建第二次执行。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("重试同一次提交") {
                        Task { await asks.retrySubmission(item.id) }
                    }
                    .font(.system(.subheadline, weight: .semibold))
                    .foregroundStyle(Palette.fogDeep)
                    .accessibilityIdentifier("ask.retry")
                }
            }
        case .queued, .running:
            SurfaceCard {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text(item.phase == .queued ? "已提交，等待服务器开始" : "服务器正在处理")
                            .font(.bodyText)
                            .foregroundStyle(Palette.ink)
                    }
                    Text("最终回答由服务器保存；离开页面不会取消执行。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                    HStack(spacing: 12) {
                        if item.serverId != nil {
                            Button(asks.cancelling.contains(item.id) ? "正在取消…" : "取消") {
                                Task { await asks.cancel(item.id) }
                            }
                            .font(.system(.subheadline, weight: .semibold))
                            .foregroundStyle(Palette.fogDeep)
                            .disabled(asks.cancelling.contains(item.id))
                            .accessibilityIdentifier("ask.cancel")
                        }
                        Button("刷新状态") {
                            Task { await asks.refresh(item.id) }
                        }
                        .font(.subheadline)
                        .foregroundStyle(Palette.fogDeep)
                    }
                }
            }
        case .succeeded:
            SurfaceCard {
                VStack(alignment: .leading, spacing: 10) {
                    if let answer = item.answer, !answer.isEmpty {
                        MarkdownAnswerView(text: answer)
                            .textSelection(.enabled)
                            .accessibilityElement(children: .contain)
                            .accessibilityIdentifier("ask.answer")
                    } else {
                        Text("服务器报告已完成，但没有返回可读回答。")
                            .font(.bodyText)
                            .foregroundStyle(Palette.amber)
                        Text("可以重新查询；如果仍然为空，这属于服务器结果异常。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                    }
                    HStack(spacing: 12) {
                        if let completed = item.completedAt {
                            Text("完成于 \(CommuteFormat.dayTime(completed))")
                                .font(.microLabel)
                                .foregroundStyle(Palette.faint)
                        }
                        Spacer()
                        if let answer = item.answer, !answer.isEmpty {
                            Button(copiedID == item.id ? "已复制" : "复制回答") {
                                UIPasteboard.general.string = answer
                                copiedID = item.id
                            }
                            .font(.subheadline)
                            .foregroundStyle(Palette.fogDeep)
                        }
                        Button("刷新") {
                            Task { await asks.refresh(item.id) }
                        }
                        .font(.subheadline)
                        .foregroundStyle(Palette.fogDeep)
                    }
                }
            }
        case .failed:
            SurfaceCard(tint: Palette.redSoft) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("这次没有完成")
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.red)
                    Text(item.errorMessage ?? "服务器未能完成这次处理。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                    HStack(spacing: 12) {
                        Button("用同一问题重新提问") {
                            question = item.question
                            focused = true
                        }
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.fogDeep)
                        if item.serverId != nil {
                            Button("刷新") { Task { await asks.refresh(item.id) } }
                                .font(.subheadline)
                                .foregroundStyle(Palette.fogDeep)
                        }
                    }
                }
            }
        case .cancelled:
            SurfaceCard {
                HStack(spacing: 8) {
                    Image(systemName: "xmark.circle")
                    Text("已取消，服务器确认后不会再产生回答。")
                        .font(.bodyText)
                        .foregroundStyle(Palette.muted)
                }
            }
        case .unknown:
            SurfaceCard(tint: Palette.amberSoft) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("处理状态暂不确定")
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Palette.amber)
                    Text("服务器仍在核对结果。可以刷新查询；不要把它当作安全重试的信号。")
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    Button("刷新核对") {
                        Task { await asks.refresh(item.id) }
                    }
                    .font(.system(.subheadline, weight: .semibold))
                    .foregroundStyle(Palette.fogDeep)
                }
            }
        }
        if let message = asks.lastError, item.isActive == false, item.phase == .failed {
            Text(message)
                .font(.captionText)
                .foregroundStyle(Palette.faint)
        }
    }

    // MARK: - 底部输入

    private var composer: some View {
        VStack(spacing: 0) {
            if let error = asks.composerError {
                Text(error)
                    .font(.captionText)
                    .foregroundStyle(Palette.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.top, 8)
            }
            HStack(spacing: 10) {
                TextField("问一件已经记下的事", text: $question, axis: .vertical)
                    .accessibilityIdentifier("ask.input")
                    .font(.bodyText)
                    .lineLimit(1...3)
                    .focused($focused)
                    .submitLabel(.send)
                    .onSubmit { send(question) }

                Button {
                    send(question)
                } label: {
                    Image(systemName: asks.submitting ? "clock" : "arrow.up")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 36, height: 36)
                        .background(canSend ? Palette.accentFill : Palette.faint)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(!canSend)
                .accessibilityIdentifier("ask.send")
                .accessibilityLabel("发送问题")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .background(.ultraThinMaterial)
        .overlay(alignment: .top) {
            Rectangle().fill(Palette.line).frame(height: 0.5)
        }
    }

    private var canSend: Bool {
        !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !asks.submitting
    }

    private var boundaryNote: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("关于这版回答")
                .font(.microLabel)
                .foregroundStyle(Palette.faint)
            Text("每次提问独立发送，当前接口没有会话上下文，不会自动带上上一轮。回答与完成时间来自 PA 服务器保存的结果；这里不展示上游服务名、工作流或调试信息。")
                .font(.captionText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
            Text("日历、提醒事项和笔记目前只在设备本地读取，还没有上传；助手只能查看 App 内已保存的内容。")
                .font(.captionText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard session.isSignedIn else {
            asks.composerError = "登录后才能向服务器提问，输入已保留。"
            showLogin = true
            return
        }
        Task {
            let ok = await asks.send(trimmed)
            if ok {
                question = ""
                focused = false
            }
        }
    }
}

// MARK: - 登录

struct PALoginSheet: View {
    @EnvironmentObject private var session: PASessionStore
    @EnvironmentObject private var asks: AskStore
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 16) {
                Text("PA 本地账号，不需要另外注册其它平台账号。")
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)

                TextField("邮箱", text: $email)
                    .accessibilityIdentifier("login.email")
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .padding(12)
                    .background(Palette.card, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Palette.line, lineWidth: 0.5))

                SecureField("密码", text: $password)
                    .accessibilityIdentifier("login.password")
                    .textContentType(.password)
                    .padding(12)
                    .background(Palette.card, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Palette.line, lineWidth: 0.5))

                if let error = session.lastError {
                    Text(error)
                        .font(.captionText)
                        .foregroundStyle(Palette.red)
                }

                PrimaryButton(title: session.isBusy ? "正在登录…" : "登录",
                              enabled: !session.isBusy && !email.isEmpty && !password.isEmpty,
                              identifier: "login.submit") {
                    Task {
                        let ok = await session.login(email: email, password: password)
                        if ok {
                            await asks.bindAccount()
                            dismiss()
                        }
                    }
                }
                Spacer()
            }
            .padding(20)
            .background(Palette.warm)
            .navigationTitle("登录 PA 账号")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
        }
    }
}
