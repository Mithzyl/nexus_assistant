import SwiftUI

struct CaptureSourceView: View {
    @EnvironmentObject private var state: AppState
    let sourceId: String

    private var source: DemoSource? {
        SampleData.demoSources.first { $0.id == sourceId }
    }

    var body: some View {
        PageShell(title: "这次交出的内容", subtitle: source?.channel) {
            VStack(alignment: .leading, spacing: 16) {
                if let source {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(source.title)
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Text(source.text)
                                .font(.bodyText)
                                .foregroundStyle(Palette.ink.opacity(0.85))
                                .lineSpacing(4)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    SurfaceCard(tint: Palette.mist.opacity(0.7)) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("这次只交了这些")
                                .font(.microLabel)
                                .foregroundStyle(Palette.fogDeep)
                            Text("你选中的这一份内容会被保存为原文依据。没有读取屏幕、相册、通知或其他应用。")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    if source.id == "ambiguous" {
                        SurfaceCard(tint: Palette.amberSoft) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text("为什么不能自动关联")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundStyle(Palette.amber)
                                Text("只有金额 ¥129，没有商家和订单号。咖啡机与收纳架金额相同，所以必须由你确认一次。")
                                    .font(.captionText)
                                    .foregroundStyle(Palette.amber.opacity(0.9))
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }

                    if source.id == "notice" {
                        SurfaceCard(tint: Palette.amberSoft) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text("为什么能关联到咖啡机")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundStyle(Palette.amber)
                                Text("订单号 DEMO-20261015-129 和金额 ¥129.00 都与原事务一致。依据是编号，不是只比金额。")
                                    .font(.captionText)
                                    .foregroundStyle(Palette.amber.opacity(0.9))
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }

                PrimaryButton(title: "回到今天") {
                    state.path.removeAll()
                    state.tab = .today
                }
            }
        }
    }
}

struct SourceView: View {
    let evidenceId: String

    private var evidence: Evidence? {
        SampleData.evidence[evidenceId]
    }

    var body: some View {
        PageShell(title: "原始依据", subtitle: evidence?.meta) {
            VStack(alignment: .leading, spacing: 16) {
                if let evidence {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(evidence.title)
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Text(evidence.text)
                                .font(.system(size: 14, design: .monospaced))
                                .foregroundStyle(Palette.ink.opacity(0.82))
                                .lineSpacing(4)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                } else {
                    Text("这份依据已经不在了。")
                        .font(.bodyText)
                        .foregroundStyle(Palette.muted)
                }

                Text("原文只用于核对和回看。事务里保存的是整理后的字段，不会改动这段原文。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct PrivacyView: View {
    @EnvironmentObject private var state: AppState

    private let dayOptions = [7, 30, 90]

    var body: some View {
        PageShell(title: "内容与隐私", subtitle: "这些开关在示例里会真的改变行为") {
            VStack(alignment: .leading, spacing: 16) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 12) {
                        Toggle(isOn: $state.preferences.keepOriginalText) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("保留交入原文")
                                    .font(.system(size: 15, weight: .medium))
                                    .foregroundStyle(Palette.ink)
                                Text(state.preferences.keepOriginalText
                                     ? "保留原文，方便回看依据"
                                     : "不保存原文，只留结构化字段和内容哈希")
                                    .font(.microLabel)
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                        .tint(Palette.fog)

                        if state.preferences.keepOriginalText {
                            DividerLine()
                            HStack(spacing: 8) {
                                Text("保留")
                                    .font(.captionText)
                                    .foregroundStyle(Palette.muted)
                                Spacer()
                                ForEach(dayOptions, id: \.self) { days in
                                    Button {
                                        state.preferences.retainDays = days
                                    } label: {
                                        Text("\(days) 天")
                                            .font(.system(size: 13, weight: state.preferences.retainDays == days ? .semibold : .regular))
                                            .foregroundStyle(state.preferences.retainDays == days ? .white : Palette.fogDeep)
                                            .padding(.horizontal, 10)
                                            .padding(.vertical, 6)
                                            .background(state.preferences.retainDays == days ? Palette.fog : Palette.mist)
                                            .clipShape(Capsule())
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                    }
                }

                SurfaceCard {
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle(isOn: $state.preferences.notifyBeforeGoingOut) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("出门前提醒")
                                    .font(.system(size: 15, weight: .medium))
                                    .foregroundStyle(Palette.ink)
                                Text("只在有清单时提醒，不推送其他内容")
                                    .font(.microLabel)
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                        .tint(Palette.fog)

                        DividerLine()
                        Text("示例里没有真实通知权限，这个开关只改变本地展示。")
                            .font(.microLabel)
                            .foregroundStyle(Palette.faint)
                    }
                }

                SurfaceCard(tint: Palette.mist.opacity(0.7)) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("关掉之后会发生什么")
                            .font(.microLabel)
                            .foregroundStyle(Palette.fogDeep)
                        bullet("新的交入不再保存原文，只留金额、时间、订单号这类字段")
                        bullet("已有事务和金额不会消失，详情里显示“原文已按保留策略删除”")
                        bullet("去重仍然生效：内容哈希会保留，同一份材料不会被记两次")
                        bullet("删掉的原文不能恢复，策略可以再打开，但历史原文回不来")
                    }
                }
            }
        }
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 7) {
            Circle()
                .fill(Palette.fog.opacity(0.6))
                .frame(width: 4, height: 4)
                .padding(.top, 6)
            Text(text)
                .font(.captionText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct SetupView: View {
    var body: some View {
        PageShell(title: "系统入口", subtitle: "控制中心、操作按钮与快捷指令") {
            VStack(alignment: .leading, spacing: 14) {
                entry(
                    icon: "switch.2",
                    title: "控制中心 · 收下这个",
                    detail: "在控制中心点亮后，选中内容可以直接交给助手。",
                    status: "未连接"
                )
                entry(
                    icon: "hand.tap",
                    title: "操作按钮 · 快捷指令",
                    detail: "锁屏侧键与快捷指令调用同一个收下动作。",
                    status: "未连接"
                )
                entry(
                    icon: "square.and.arrow.down",
                    title: "分享菜单",
                    detail: "从其他应用分享页面或文本进来。",
                    status: "未连接"
                )

                SurfaceCard(tint: Palette.amberSoft) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("为什么在示例里是未连接")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Palette.amber)
                        Text("这些入口需要原生扩展和系统授权（App Intents、控制中心控件、分享扩展）。当前是本地示例，没有申请任何权限。")
                            .font(.captionText)
                            .foregroundStyle(Palette.amber.opacity(0.9))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func entry(icon: String, title: String, detail: String, status: String) -> some View {
        SurfaceCard(padding: 14) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.fog)
                    .frame(width: 30, height: 30)
                    .background(Palette.mist)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(Palette.ink)
                    Text(detail)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 6)
                TagPill(text: status, tone: .amber)
            }
        }
    }
}

struct InfoView: View {
    var body: some View {
        PageShell(title: "关于这个示例", subtitle: "V3 交互原型的 SwiftUI 版本") {
            VStack(alignment: .leading, spacing: 14) {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("它是什么")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Palette.ink)
                        Text("把 V3 原型的四个页面和主要流程，用 SwiftUI 重写了一份可运行的本地样例。数据、AI 回答和状态变化都是本机示例。")
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                SurfaceCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("它不是什么")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Palette.ink)
                        bullet("没有接入真实 AI、支付、日历、通知或相册")
                        bullet("没有连接后端，也没有账号体系")
                        bullet("退出应用后数据回到初始示例")
                        bullet("控制中心与快捷指令尚未接入")
                    }
                }

                Text("示例日期固定为 2026 年 10 月 23 日。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
            }
        }
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 7) {
            Circle()
                .fill(Palette.faint)
                .frame(width: 4, height: 4)
                .padding(.top, 6)
            Text(text)
                .font(.captionText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
