import SwiftUI

struct CaptureSheet: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss
    let preset: String?

    private enum Stage { case choose, processing, result }

    @State private var mode = 0
    @State private var sourceId = "notice"
    @State private var text = ""
    @State private var stage: Stage = .choose
    @State private var outcome: CaptureOutcome?
    @State private var showChoices = false
    @State private var error = ""
    @FocusState private var focused: Bool

    private var source: DemoSource {
        SampleData.demoSources.first { $0.id == sourceId } ?? SampleData.demoSources[0]
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                switch stage {
                case .choose: chooser
                case .processing: processing
                case .result: resultView
                }
            }
            .padding(.horizontal, Spacing.screen)
            .padding(.top, 18)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .background(Palette.warm)
        .presentationCornerRadius(28)
        .presentationDragIndicator(.visible)
        .onAppear {
            if let preset {
                sourceId = preset
            }
            if state.captureSayMode {
                mode = 1
            }
        }
        .task {
            // 从首页“记一句”进入时，直接把光标放进输入框。
            guard state.captureSayMode else { return }
            try? await Task.sleep(for: .milliseconds(350))
            focused = true
        }
    }

    private var chooser: some View {
        VStack(alignment: .leading, spacing: 16) {
            modeSwitch

            Text(mode == 0 ? "把这份内容，交给助手。" : "想到什么，顺手留一句。")
                .font(.system(size: 24, weight: .semibold, design: .serif))
                .foregroundStyle(Palette.ink)

            Text(mode == 0
                 ? "不用选择记账还是待办。先收下，再关联到对应的事。"
                 : "这里用输入文字体验口述，不会调用麦克风。")
                .font(.bodyText)
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)

            if mode == 0 {
                sourcePicker
            } else {
                sayComposer
            }

            if !error.isEmpty {
                Text(error)
                    .font(.captionText)
                    .foregroundStyle(Palette.red)
            }

            PrimaryButton(title: mode == 0 ? "收下这个" : "把这句话交给助手") {
                run()
            }

            Text("仅整理你在这里选中的示例。不会读取真实屏幕、通知、相册或其他应用。")
                .font(.captionText)
                .foregroundStyle(Palette.faint)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var modeSwitch: some View {
        HStack(spacing: 6) {
            switchButton(title: "收下这个", icon: "square.and.arrow.down", index: 0)
            switchButton(title: "记一句", icon: "text.bubble", index: 1)
        }
        .padding(4)
        .background(Palette.mist)
        .clipShape(RoundedRectangle(cornerRadius: 13, style: .continuous))
    }

    private func switchButton(title: String, icon: String, index: Int) -> some View {
        Button {
            mode = index
            error = ""
        } label: {
            HStack(spacing: 5) {
                Image(systemName: icon)
                Text(title)
            }
            .font(.system(size: 14, weight: mode == index ? .semibold : .regular))
            .foregroundStyle(mode == index ? Palette.fogDeep : Palette.muted)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background(mode == index ? Palette.card : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private var sourcePicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("本次交来的内容 · 示例")
                    .font(.microLabel)
                    .foregroundStyle(Palette.faint)
                Spacer()
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        showChoices.toggle()
                    }
                } label: {
                    HStack(spacing: 3) {
                        Text(showChoices ? "收起示例" : "换一个示例")
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .semibold))
                            .rotationEffect(.degrees(showChoices ? 90 : 0))
                    }
                    .font(.microLabel)
                    .foregroundStyle(Palette.fogDeep)
                }
                .buttonStyle(.plain)
            }

            if showChoices {
                VStack(spacing: 6) {
                    ForEach(SampleData.demoSources) { item in
                        Button {
                            sourceId = item.id
                            withAnimation(.easeInOut(duration: 0.18)) {
                                showChoices = false
                            }
                        } label: {
                            HStack(spacing: 10) {
                                Image(systemName: item.id == sourceId ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(item.id == sourceId ? Palette.fog : Palette.faint)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.title)
                                        .font(.system(size: 14, weight: .medium))
                                        .foregroundStyle(Palette.ink)
                                    Text(item.hint)
                                        .font(.microLabel)
                                        .foregroundStyle(Palette.muted)
                                }
                                Spacer()
                            }
                            .padding(10)
                            .background(item.id == sourceId ? Palette.mist : Palette.card)
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
            } else {
                SurfaceCard(padding: 14) {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 6) {
                            Image(systemName: "doc.text")
                                .font(.system(size: 12))
                            Text(source.channel)
                                .font(.microLabel)
                            Spacer()
                        }
                        .foregroundStyle(Palette.faint)

                        Text(source.title)
                            .font(.cardTitle)
                            .foregroundStyle(Palette.ink)

                        Text(source.text)
                            .font(.captionText)
                            .foregroundStyle(Palette.muted)
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)

                        DividerLine()

                        Button {
                            dismiss()
                            state.open(.captureSource(source.id))
                        } label: {
                            HStack(spacing: 6) {
                                Image(systemName: "doc.text.magnifyingglass")
                                Text("先看这次交出的内容")
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
            }
        }
    }

    private var sayComposer: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("例如：下次去江边，记得带件外套。", text: $text, axis: .vertical)
                .font(.bodyText)
                .lineLimit(4...6)
                .focused($focused)
                .padding(14)
                .background(Palette.card)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Palette.line, lineWidth: 1))

            HStack(spacing: 8) {
                exampleChip("记住一点经验") { text = "下次去江边，记得带件外套。" }
                exampleChip("留住一个念头") { text = "想去柳江边散散步，留60分钟。" }
            }
        }
    }

    private func exampleChip(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.microLabel)
                .foregroundStyle(Palette.fogDeep)
                .padding(.horizontal, 11)
                .padding(.vertical, 7)
                .background(Palette.mist)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    private var processing: some View {
        VStack(spacing: 18) {
            ProgressView()
                .tint(Palette.fog)
                .scaleEffect(1.15)
            Text("收到了，正在理一理。")
                .font(.system(size: 18, weight: .semibold, design: .serif))
                .foregroundStyle(Palette.ink)
            Text(mode == 0 ? "先核对订单号和金额，再决定归到哪件事。" : "看看这句话属于经验、心愿，还是暂时归不了类。")
                .font(.captionText)
                .foregroundStyle(Palette.muted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
    }

    @ViewBuilder
    private var resultView: some View {
        if let outcome {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 8) {
                    Image(systemName: outcome.duplicate ? "arrow.triangle.2.circlepath" : "checkmark.circle.fill")
                        .foregroundStyle(outcome.duplicate ? Palette.amber : Palette.green)
                    Text(outcome.duplicate ? "没有重复处理" : "理好了")
                        .font(.microLabel)
                        .foregroundStyle(outcome.duplicate ? Palette.amber : Palette.green)
                }

                Text(outcome.title)
                    .font(.system(size: 24, weight: .semibold, design: .serif))
                    .foregroundStyle(Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)

                Text(outcome.body)
                    .font(.bodyText)
                    .foregroundStyle(Palette.muted)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)

                if let route = outcome.route {
                    PrimaryButton(title: "去看看") {
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.32) {
                            state.open(route)
                        }
                    }
                }

                SecondaryButton(title: "再交一份") {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        stage = .choose
                        text = ""
                        error = ""
                        showChoices = false
                    }
                }
            }
            .padding(.top, 8)
        }
    }

    private func run() {
        focused = false
        if mode == 1 && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            error = "先记一句，或试试下面的示例。"
            return
        }
        error = ""
        withAnimation(.easeInOut(duration: 0.2)) {
            stage = .processing
        }
        let capturedText = text
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.85) {
            let result = mode == 0 ? state.ingest(source: source) : state.ingest(text: capturedText)
            withAnimation(.easeInOut(duration: 0.25)) {
                outcome = result
                stage = .result
            }
        }
    }
}
