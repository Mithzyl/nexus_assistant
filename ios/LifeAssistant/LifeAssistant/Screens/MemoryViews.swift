import SwiftUI

struct MemoriesView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        PageShell(
            title: "个人记忆",
            subtitle: "\(state.memories.filter(\.enabled).count) 条生效，停用的不会被助手引用",
            trailing: "\(state.memories.count) 条"
        ) {
            VStack(alignment: .leading, spacing: 12) {
                if state.memories.isEmpty {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("还没有记忆")
                                .font(.cardTitle)
                                .foregroundStyle(Palette.ink)
                            Text("你主动记下的经验会出现在这里，助手不会凭空生成。")
                                .font(.captionText)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                } else {
                    ForEach(state.memories) { memory in
                        memoryCard(memory)
                    }
                }

                Text("记忆只保存你确认过的内容。停用后立即从清单与回答依据里移除，不会删除原文。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)

                SecondaryButton(title: "再记一条经验") {
                    state.open(.lesson)
                }
            }
        }
    }

    private func memoryCard(_ memory: MemoryItem) -> some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: memory.enabled ? "brain" : "brain.head.profile")
                        .font(.system(size: 15))
                        .foregroundStyle(memory.enabled ? Palette.fog : Palette.faint)
                        .frame(width: 30, height: 30)
                        .background(memory.enabled ? Palette.mist : Color(hex: 0xF1F1EC))
                        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

                    Button {
                        state.open(.memory(memory.id))
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(memory.text)
                                .font(.system(size: 15, weight: .medium))
                                .foregroundStyle(memory.enabled ? Palette.ink : Palette.faint)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                            Text("\(memory.place) · \(memory.date)")
                                .font(.microLabel)
                                .foregroundStyle(Palette.muted)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.plain)

                    Toggle("", isOn: Binding(
                        get: { memory.enabled },
                        set: { _ in state.toggleMemory(memory.id) }
                    ))
                    .labelsHidden()
                    .tint(Palette.fog)
                    .scaleEffect(0.8)
                }

                if !memory.enabled {
                    Text("已停用 · 助手不会引用这条")
                        .font(.microLabel)
                        .foregroundStyle(Palette.faint)
                }
            }
        }
    }
}

struct MemoryDetailView: View {
    @EnvironmentObject private var state: AppState
    let memoryId: String

    @State private var editing = false
    @State private var draft = ""

    private var memory: MemoryItem? {
        state.memories.first { $0.id == memoryId }
    }

    var body: some View {
        PageShell(title: "这条经验", subtitle: memory?.place) {
            VStack(alignment: .leading, spacing: 16) {
                if let memory {
                    SurfaceCard {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack {
                                Text("内容")
                                    .font(.microLabel)
                                    .foregroundStyle(Palette.faint)
                                Spacer()
                                Button(editing ? "取消" : "编辑") {
                                    draft = memory.text
                                    editing.toggle()
                                }
                                .font(.microLabel)
                                .foregroundStyle(Palette.fogDeep)
                                .buttonStyle(.plain)
                            }

                            if editing {
                                TextEditor(text: $draft)
                                    .font(.bodyText)
                                    .frame(minHeight: 90)
                                    .scrollContentBackground(.hidden)
                                    .padding(10)
                                    .background(Palette.mist.opacity(0.6))
                                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))

                                PrimaryButton(title: "保存修改", enabled: !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) {
                                    state.updateMemory(memory.id, text: draft.trimmingCharacters(in: .whitespacesAndNewlines))
                                    editing = false
                                }
                            } else {
                                Text(memory.text)
                                    .font(.system(size: 17, weight: .medium, design: .serif))
                                    .foregroundStyle(Palette.ink)
                                    .fixedSize(horizontal: false, vertical: true)
                            }

                            DividerLine()

                            HStack {
                                Text("启用")
                                    .font(.captionText)
                                    .foregroundStyle(Palette.muted)
                                Spacer()
                                Toggle("", isOn: Binding(
                                    get: { memory.enabled },
                                    set: { _ in state.toggleMemory(memory.id) }
                                ))
                                .labelsHidden()
                                .tint(Palette.fog)
                            }

                            Text(memory.enabled ? "助手会在相关回答里引用这条，并标出来源。" : "已停用，助手不会再引用，但内容仍保存在这里。")
                                .font(.microLabel)
                                .foregroundStyle(Palette.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    SurfaceCard(tint: Palette.mist.opacity(0.7)) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("最初从哪来")
                                .font(.microLabel)
                                .foregroundStyle(Palette.fogDeep)
                            Text(memory.source)
                                .font(.captionText)
                                .foregroundStyle(Palette.ink.opacity(0.85))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                } else {
                    Text("这条记忆已经不在了。")
                        .font(.bodyText)
                        .foregroundStyle(Palette.muted)
                }
            }
        }
    }
}

struct LessonView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var text = ""
    @State private var saved = false
    @FocusState private var focused: Bool

    var body: some View {
        PageShell(title: "这次有什么想记住？", subtitle: "只保存你写下的原话") {
            VStack(alignment: .leading, spacing: 16) {
                TextEditor(text: $text)
                    .font(.bodyText)
                    .frame(minHeight: 130)
                    .scrollContentBackground(.hidden)
                    .padding(12)
                    .background(Palette.card)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(Palette.line, lineWidth: 1)
                    )
                    .focused($focused)

                Text("例子：这家影院空调冷，下次带件薄外套。")
                    .font(.microLabel)
                    .foregroundStyle(Palette.faint)

                PrimaryButton(title: "保存到个人记忆", enabled: !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) {
                    state.addLesson(text)
                    saved = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                        dismiss()
                    }
                }

                if saved {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(Palette.green)
                        Text("已保存")
                            .font(.captionText)
                            .foregroundStyle(Palette.green)
                    }
                }

                Text("保存后仍可编辑或停用。它不是模型推测出来的结论。")
                    .font(.captionText)
                    .foregroundStyle(Palette.faint)
            }
        }
        .onAppear { focused = true }
    }
}
