import SwiftUI

struct CommuteNotificationsView: View {
    private let api = CommuteAPIClient()

    @State private var status: BarkStatusResponse?
    @State private var keyInput = ""
    @State private var isLoading = true
    @State private var isSaving = false
    @State private var isTesting = false
    @State private var message: String?
    @State private var showUnbind = false
    @State private var showTest = false

    var body: some View {
        PageShell(title: "通知设置", subtitle: "通过 Bark 接收提醒，不冒充 App 原生推送") {
            VStack(alignment: .leading, spacing: 16) {
                Text("通知摘要会交给 Bark 服务，再由 Bark 发送到你的 iPhone。只有你主动开启后，服务器才会定时发送。")
                    .font(.bodyText).foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)

                if isLoading {
                    ProgressView("正在读取当前账号的 Bark 状态…")
                } else if let status {
                    statusCard(status)
                    bindingCard(status)
                    if status.configured {
                        reminderCard(status)
                    }
                } else {
                    unavailableCard
                }

                if let message {
                    SurfaceCard(tint: Palette.mist) {
                        Text(message).font(.captionText).foregroundStyle(Palette.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                Text("Bark 接受请求不等于手机已收到。关闭提醒只影响后续发送，已发出的通知无法撤回。")
                    .font(.captionText).foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .task { await load() }
        .alert("解绑 Bark？", isPresented: $showUnbind) {
            Button("取消", role: .cancel) {}
            Button("解绑", role: .destructive) { Task { await unbind() } }
        } message: {
            Text("解绑后停止自动提醒，并删除服务器保存的 Key。已发出的通知无法撤回。")
        }
        .alert("发送测试通知？", isPresented: $showTest) {
            Button("取消", role: .cancel) {}
            Button("发送") { Task { await test() } }
        } message: {
            Text("会真实调用 Bark 一次，但不会开启定时提醒。")
        }
    }

    private func stateText(_ status: BarkStatusResponse) -> String {
        if status.state == "enabled" { return "已开启自动提醒" }
        if status.state == "disabled" { return "Key 已绑定，自动提醒关闭" }
        return "尚未绑定 Bark"
    }

    private func statusCard(_ status: BarkStatusResponse) -> some View {
        SurfaceCard(tint: Palette.mist) {
            VStack(alignment: .leading, spacing: 6) {
                Text("当前账号").font(.microLabel).foregroundStyle(Palette.fogDeep)
                Text(stateText(status)).font(.cardTitle).foregroundStyle(Palette.ink)
                if let last = status.lastTestStatus {
                    Text("最近一次测试：\(testLabel(last))").font(.captionText).foregroundStyle(Palette.muted)
                }
                Text("绑定状态来自 PA 后端；重新打开 App 会再次读取。")
                    .font(.captionText).foregroundStyle(Palette.faint)
            }
        }
    }

    private func bindingCard(_ status: BarkStatusResponse) -> some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 10) {
                Text(status.configured ? "替换 Key" : "绑定 Bark").font(.cardTitle)
                Text("Key 只提交给当前登录账号，后端不会在状态、日志或错误中返回它。")
                    .font(.captionText).foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
                SecureField("Bark Device Key", text: $keyInput)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                PrimaryButton(title: status.configured ? "替换并保存" : "保存绑定",
                              enabled: !keyInput.trimmingCharacters(in: .whitespaces).isEmpty && !isSaving) {
                    Task { await bind() }
                }
                if status.configured {
                    SecondaryButton(title: "解绑 Bark") { showUnbind = true }
                }
                Text("保存不会自动开启定时提醒，也不会自动发送测试。")
                    .font(.captionText).foregroundStyle(Palette.faint)
            }
        }
    }

    private func reminderCard(_ status: BarkStatusResponse) -> some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 10) {
                Toggle(isOn: Binding(
                    get: { status.autoRemindersEnabled },
                    set: { newValue in Task { await setAuto(newValue) } }
                )) {
                    Text("允许服务器定时发送通勤提醒").font(.bodyText)
                }
                .tint(Palette.fog)
                .disabled(isSaving || isTesting)
                Text("关闭后保存到后端；不会假装关闭成功。")
                    .font(.captionText).foregroundStyle(Palette.faint)
                SecondaryButton(title: isTesting ? "正在发送…" : "发送测试通知") { showTest = true }
                    .disabled(isTesting || isSaving)
            }
        }
    }

    private var unavailableCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("暂时无法读取 Bark 状态").font(.cardTitle)
                Text("请确认已登录且 PA 后端可用。此处不会显示虚构的已绑定状态。")
                    .font(.captionText).foregroundStyle(Palette.muted)
                SecondaryButton(title: "重试") { Task { await load() } }
            }
        }
    }

    private func load() async {
        isLoading = true; message = nil
        do { status = try await api.barkStatus() }
        catch { status = nil; message = "读取失败：\(error.localizedDescription)" }
        isLoading = false
    }

    private func bind() async {
        let key = keyInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard key.count >= 8 else { message = "Key 不完整，未保存。"; return }
        isSaving = true; message = nil
        do {
            let updated = try await api.bindBark(deviceKey: key)
            status = updated; keyInput = ""
            message = "后端已确认保存。自动提醒仍为关闭；需要时再手动开启。"
        } catch { message = "保存失败：\(error.localizedDescription)" }
        isSaving = false
    }

    private func unbind() async {
        isSaving = true; message = nil
        do { status = try await api.unbindBark(); message = "后端已解绑并删除 Key。" }
        catch { message = "解绑失败：\(error.localizedDescription)" }
        isSaving = false
    }

    private func setAuto(_ enabled: Bool) async {
        isSaving = true; message = nil
        do {
            status = try await api.setBarkAutoReminders(enabled)
            message = enabled ? "后端已确认开启自动提醒。" : "后端已确认关闭自动提醒。"
        } catch { message = "设置失败，未假装已保存：\(error.localizedDescription)" }
        isSaving = false
    }

    private func test() async {
        isTesting = true; message = nil
        do {
            let result = try await api.testBark()
            switch result.status {
            case "accepted": message = "Bark 已接受请求。请以手机是否收到为准，不要仅凭这里判断送达。"
            case "failed": message = "发送失败：\(result.message)"
            default: message = "结果暂不确定：\(result.message) 不会自动重发。"
            }
            status = try? await api.barkStatus()
        } catch { message = "测试请求失败：\(error.localizedDescription)" }
        isTesting = false
    }

    private func testLabel(_ raw: String) -> String {
        switch raw {
        case "accepted": return "Bark 已接受"
        case "failed": return "发送失败"
        default: return "结果暂不确定"
        }
    }
}
