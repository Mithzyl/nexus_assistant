import SwiftUI
import UIKit

// MARK: - 数据来源总览

struct DataSourcesView: View {
    @EnvironmentObject private var state: AppState
    @StateObject private var sources = DataSourceStore.shared
    @StateObject private var notes = NoteImportStore.shared
    @State private var showClaimConfirm = false
    @State private var showCloud = false

    var body: some View {
        PageShell(title: "数据来源", subtitle: "管理在 App 中查看的日程、提醒与笔记") {
            SurfaceCard {
                VStack(alignment: .leading, spacing: 8) {
                    Text("内容仅保存在本机，不会修改原数据。")
                        .font(.bodyText)
                        .foregroundStyle(Palette.ink)
                    SecondaryButton(title: "管理云端同步与已上传内容") { showCloud = true }
                    Text("只有你明确上传的范围内内容才会进入 PA；本机可见不等于云端已上传。")
                        .font(.captionText).foregroundStyle(Palette.faint)
                }
            }

            if sources.hasUnclaimedLegacy || notes.hasUnclaimedLegacy {
                SurfaceCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("发现旧版本的本机数据").font(.bodyText.weight(.semibold)).foregroundStyle(Palette.ink)
                        Text("这些内容尚未归属任何账号。认领后会归入当前账号；不认领会继续隔离保留，不会自动删除或展示。")
                            .font(.captionText).foregroundStyle(Palette.muted)
                            .fixedSize(horizontal: false, vertical: true)
                        if sources.hasUnclaimedLegacy || notes.hasUnclaimedLegacy {
                            SecondaryButton(title: "认领这批旧数据") { showClaimConfirm = true }
                            Text("认领后其他账号不能再认领同一批数据；系统会保留隔离备份。")
                                .font(.captionText).foregroundStyle(Palette.faint)
                        }
                    }
                }
            }

            VStack(spacing: 0) {
                sourceRow(.calendar, subtitle: "过去 7 天到未来 30 天", status: sources.state(for: .calendar).title)
                DividerLine()
                sourceRow(.reminders, subtitle: "仅未完成项", status: sources.state(for: .reminders).title)
                DividerLine()
                sourceRow(.notes,
                          subtitle: notes.notes.isEmpty ? "粘贴正文并预览" : "已导入 \(notes.notes.count) 篇",
                          status: notes.notes.isEmpty ? "查看方式" : "已导入")
            }
            .background(Palette.card)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .strokeBorder(Palette.line.opacity(0.7), lineWidth: 0.5)
            )
        }
        .onAppear {
            if ProcessInfo.processInfo.environment["START_CLOUD"] == "1" { showCloud = true }
        }
        .sheet(isPresented: $showCloud) { CloudSyncView() }
        .confirmationDialog("确认认领旧数据？", isPresented: $showClaimConfirm, titleVisibility: .visible) {
            Button("我确认这是本账号的旧数据", role: .destructive) {
                _ = sources.claimLegacyData()
                _ = notes.claimLegacyData()
            }
            Button("继续隔离", role: .cancel) {}
        } message: {
            Text("点击无法证明数据属于哪个账号。只有确认是本账号旧数据时再认领；认领后其他账号不能再认领，原文件会保留在隔离备份中，不会删除。")
        }
    }

    private func sourceRow(_ kind: SourceKind, subtitle: String, status: String) -> some View {
        Button {
            state.open(kind == .notes ? .noteImport : .sourceDetail(kind.rawValue))
        } label: {
            HStack(spacing: 12) {
                Image(systemName: kind.symbol)
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(Palette.fogDeep)
                    .frame(width: 34, height: 34)
                    .background(Palette.card)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                VStack(alignment: .leading, spacing: 3) {
                    Text(kind.title)
                        .font(.system(.body, design: .rounded, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                    Text(subtitle)
                        .font(.captionText)
                        .foregroundStyle(Palette.muted)
                        .lineLimit(1)
                }
                Spacer()
                Text(status)
                    .font(.captionText)
                    .foregroundStyle(status.contains("失败") || status.contains("未允许") || status.contains("撤回") ? Palette.amber : Palette.fogDeep)
                    .multilineTextAlignment(.trailing)
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Palette.faint)
            }
            .padding(Spacing.card)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(kind == .notes ? "sources.notes" : (kind == .calendar ? "sources.calendar" : "sources.reminders"))
    }
}

// MARK: - 日历 / 提醒事项详情

struct SourceDetailView: View {
    let kind: SourceKind
    @EnvironmentObject private var state: AppState
    @StateObject private var sources = DataSourceStore.shared
    @State private var draft: Set<String> = []
    @State private var confirmRemove = false
    @State private var didLoad = false

    private var selection: SourceSelection { sources.selection(kind) }
    private var currentState: SourceConnectionState { sources.state(for: kind) }

    var body: some View {
        PageShell(title: kind.title) {
            purposeCard
            statusCard

            if currentState.isConnected {
                scopeCard
                selectionCard
                previewCard
                privacyCard
                maintenanceCard
            }
        }
        .task {
            guard !didLoad else { return }
            didLoad = true
            draft = sources.selection(kind).selectedIDs
            if sources.provider.permission(for: kind) == .fullAccess {
                sources.refresh(kind)
            }
        }
        .onChange(of: sources.accountID) { _, _ in
            draft = sources.selection(kind).selectedIDs
            if sources.provider.permission(for: kind) == .fullAccess {
                sources.refresh(kind)
            }
        }
        .confirmationDialog("移除本机保存的副本？", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("移除副本", role: .destructive) { sources.removeLocalCopy(for: kind) }
            Button("取消", role: .cancel) {}
        } message: {
            Text("只删除 App 保存的这份内容，不改变设备权限，也不会影响系统里的原数据。")
        }
    }

    private var purposeCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text(kind == .calendar ? "在 App 中查看你选择的日程。" : "在 App 中查看你选择的未完成提醒。")
                    .font(.cardTitle)
                    .foregroundStyle(Palette.ink)
                Text("当前版本不会修改原\(kind == .calendar ? "日程" : "提醒")。系统会请求完整访问，App 只读取你勾选的范围。")
                    .font(.captionText)
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var statusCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 10) {
                Text(currentState.title)
                    .font(.system(.body, design: .rounded, weight: .semibold))
                    .foregroundStyle(statusColor)
                switch currentState {
                case .notConnected:
                    Text("还没有请求系统权限。只有你点下面的按钮时才会弹窗。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                    PrimaryButton(title: sources.isRequesting.contains(kind) ? "正在请求…" : "连接\(kind.title)", enabled: !sources.isRequesting.contains(kind)) {
                        Task {
                            let granted = await sources.connect(kind)
                            if granted { draft = sources.selection(kind).selectedIDs }
                        }
                    }
                case .permissionDenied, .permissionRevoked:
                    Text("App 目前读不到\(kind.title)。这不等于“没有内容”。可以到系统设置里重新允许只读访问。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    SecondaryButton(title: "打开系统设置") {
                        if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                    }
                case .grantedNoSelection:
                    Text("系统权限正常，但还没有勾选范围。请在下面选择，App 不会默认全选。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                case .selectedNotRead:
                    Text("已勾选范围，但还没有读取或本机副本已移除。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                case .selectedEmpty:
                    Text("已选范围里目前没有内容；这与读取失败不同。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                case .failed(let message):
                    Text(message)
                        .font(.captionText).foregroundStyle(Palette.amber)
                    if let last = selection.lastReadAt {
                        Text("上次成功读取：\(last.formatted(date: .abbreviated, time: .shortened))；当前显示的是那次快照。")
                            .font(.captionText).foregroundStyle(Palette.muted)
                    }
                    SecondaryButton(title: "重试读取") { sources.refresh(kind) }
                case .ready(let last, let count):
                    Text("最近成功读取：\(last.formatted(date: .abbreviated, time: .shortened)) · \(count) 项")
                        .font(.captionText).foregroundStyle(Palette.muted)
                case .paused(let last, let count):
                    Text(last.map { "上次读取：\($0.formatted(date: .abbreviated, time: .shortened))" } ?? "还没有成功读取过")
                        .font(.captionText).foregroundStyle(Palette.muted)
                    Text("已暂停后续读取；本机保留 \(count) 项旧副本。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                }
            }
        }
    }

    private var scopeCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Text("读取范围").font(.system(.subheadline, weight: .semibold)).foregroundStyle(Palette.ink)
                Text(kind.readScope).font(.captionText).foregroundStyle(Palette.muted)
                Text("这是产品读取范围，不是系统访问权限的限制。")
                    .font(.captionText).foregroundStyle(Palette.faint)
            }
        }
    }

    private var selectionCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 12) {
                Text("选择范围").font(.system(.subheadline, weight: .semibold)).foregroundStyle(Palette.ink)
                if sources.groups(for: kind).isEmpty {
                    Text("系统里没有可选的\(kind.title)。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                } else {
                    ForEach(sources.groups(for: kind)) { group in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(group.title)
                                .font(.system(.caption, design: .rounded, weight: .semibold))
                                .foregroundStyle(Palette.fogDeep)
                            ForEach(group.sources) { source in
                                Button {
                                    if draft.contains(source.id) { draft.remove(source.id) } else { draft.insert(source.id) }
                                } label: {
                                    HStack(spacing: 10) {
                                        Image(systemName: draft.contains(source.id) ? "checkmark.circle.fill" : "circle")
                                            .foregroundStyle(draft.contains(source.id) ? Palette.fogDeep : Palette.faint)
                                        Text(source.title).font(.bodyText).foregroundStyle(Palette.ink)
                                        Spacer()
                                        if !source.subtitle.isEmpty {
                                            Text(source.subtitle).font(.captionText).foregroundStyle(Palette.faint)
                                        }
                                    }
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    PrimaryButton(title: "预览并启用", enabled: !draft.isEmpty) {
                        sources.applySelection(draft, for: kind)
                    }
                    if draft.isEmpty {
                        Text("不勾选就不会读取任何\(kind.title)。")
                            .font(.captionText).foregroundStyle(Palette.faint)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var previewCard: some View {
        let items = sources.items(kind)
        if !selection.selectedIDs.isEmpty {
            SurfaceCard {
                VStack(alignment: .leading, spacing: 10) {
                    Text("内容预览").font(.system(.subheadline, weight: .semibold)).foregroundStyle(Palette.ink)
                    if items.isEmpty && selection.lastReadAt != nil {
                        Text("已选范围内没有读到内容。")
                            .font(.captionText).foregroundStyle(Palette.muted)
                    } else if items.isEmpty {
                        Text("启用后会显示预览；预览来自本机读取。")
                            .font(.captionText).foregroundStyle(Palette.muted)
                    } else {
                        ForEach(items.prefix(6)) { item in
                            HStack(alignment: .top) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.title).font(.bodyText).foregroundStyle(Palette.ink)
                                    Text(item.subtitle).font(.captionText).foregroundStyle(Palette.faint)
                                }
                                Spacer()
                                if let date = item.date {
                                    Text(date.formatted(date: .abbreviated, time: .shortened))
                                        .font(.captionText).foregroundStyle(Palette.muted)
                                }
                            }
                        }
                        if items.count > 6 {
                            Text("仅显示前 6 项；共 \(items.count) 项本机预览。")
                                .font(.captionText).foregroundStyle(Palette.faint)
                        }
                    }
                }
            }
        }
    }

    private var privacyCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 10) {
                Text("用途").font(.system(.subheadline, weight: .semibold)).foregroundStyle(Palette.ink)
                HStack(spacing: 8) {
                    Image(systemName: "iphone")
                        .foregroundStyle(Palette.fogDeep)
                    Text("只在这台设备展示").font(.bodyText).foregroundStyle(Palette.ink)
                    Spacer()
                    Text("当前").font(.captionText).foregroundStyle(Palette.faint)
                }
                Text("本批只做本机只读，不会把内容上传到 PA 或模型服务。")
                    .font(.captionText).foregroundStyle(Palette.muted)
                DividerLine()
                HStack(spacing: 8) {
                    Image(systemName: "cloud")
                        .foregroundStyle(Palette.faint)
                    Text("让云端助手使用选定内容").font(.bodyText).foregroundStyle(Palette.faint)
                    Spacer()
                    Text("尚未接入").font(.captionText).foregroundStyle(Palette.faint)
                }
                Text("上传、撤回与删除已上传副本还没实现；这里不会先放一个假开关。")
                    .font(.captionText).foregroundStyle(Palette.faint)
            }
        }
    }

    private var maintenanceCard: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 12) {
                Text("维护").font(.system(.subheadline, weight: .semibold)).foregroundStyle(Palette.ink)
                Toggle("暂停读取", isOn: Binding(
                    get: { selection.paused },
                    set: { sources.setPaused($0, for: kind) }
                ))
                .tint(Palette.fogDeep)
                Text("暂停只停止后续读取，不会删除已保存的本机副本，也不会改变系统权限。")
                    .font(.captionText).foregroundStyle(Palette.faint)

                Button(role: .destructive) { confirmRemove = true } label: {
                    Text("移除已保存的本机副本").font(.system(.subheadline, weight: .semibold))
                }
                .disabled(sources.items(kind).isEmpty)

                Text("撤销系统权限只能阻止后续读取，不会让已经保存的副本自动消失；本批没有上传副本。")
                    .font(.captionText).foregroundStyle(Palette.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var statusColor: Color {
        switch currentState {
        case .failed, .permissionRevoked, .permissionDenied: return Palette.amber
        case .ready, .paused: return Palette.fogDeep
        default: return Palette.ink
        }
    }
}

private extension SourceConnectionState {
    var isConnected: Bool {
        switch self {
        case .grantedNoSelection, .selectedNotRead, .selectedEmpty, .ready, .paused, .failed, .permissionRevoked:
            return true
        case .notConnected, .permissionDenied: return false
        }
    }
}

// MARK: - 备忘录导入

struct NoteImportView: View {
    @EnvironmentObject private var state: AppState
    @StateObject private var notes = NoteImportStore.shared
    @State private var showPaste = false
    @State private var showShareHelp = false
    @State private var draftTitle = ""
    @State private var draftBody = ""
    @State private var draftMatter: String?
    @State private var duplicateExact: ImportedNote?
    @State private var showDuplicateDialog = false

    var body: some View {
        PageShell(title: "备忘录导入", subtitle: "由你选择带进来的内容") {
            SurfaceCard {
                VStack(alignment: .leading, spacing: 10) {
                    Text("把需要的笔记带进来").font(.title3.weight(.semibold)).foregroundStyle(Palette.ink)
                    Text("当前先支持粘贴正文并预览；分享扩展会在后续版本提供。不会扫描整个备忘录。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    PrimaryButton(title: "粘贴并预览", identifier: "notes.paste") { showPaste = true }
                    HStack(spacing: 10) {
                        SecondaryButton(title: "支持哪些导入方式") { showShareHelp = true }
                        SecondaryButton(title: "了解快捷指令导入") { showShareHelp = true }
                    }
                }
            }

            if notes.notes.isEmpty {
                SurfaceCard {
                    Text("还没有导入的笔记。粘贴一段内容后会先进入预览，不会直接保存。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                }
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    SectionHeading(title: "已导入")
                    ForEach(notes.notes) { note in
                        SurfaceCard {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(note.title).font(.system(.body, design: .rounded, weight: .semibold)).foregroundStyle(Palette.ink)
                                Text(note.body).font(.captionText).foregroundStyle(Palette.muted).lineLimit(2)
                                HStack {
                                    Text(note.matterTitle ?? "未关联事务").font(.captionText).foregroundStyle(Palette.faint)
                                    Spacer()
                                    Text(note.importedAt.formatted(date: .abbreviated, time: .shortened))
                                        .font(.captionText).foregroundStyle(Palette.faint)
                                }
                                Button(role: .destructive) { notes.delete(note.id) } label: {
                                    Text("删除本机副本").font(.captionText.weight(.semibold))
                                }
                            }
                        }
                    }
                }
            }
        }
        .onChange(of: notes.accountID) { _, _ in
            showPaste = false
            showShareHelp = false
            showDuplicateDialog = false
            draftTitle = ""
            draftBody = ""
            draftMatter = nil
            duplicateExact = nil
        }
        .sheet(isPresented: $showShareHelp) { shareHelp }
        .sheet(isPresented: $showPaste) { pasteSheet }
        .confirmationDialog("已导入过相同内容", isPresented: $showDuplicateDialog, titleVisibility: .visible) {
            Button("保留两份") { saveNote(replaceID: nil) }
            if let exact = duplicateExact {
                Button("替换已导入副本", role: .destructive) { saveNote(replaceID: exact.id) }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("内容相同。替换只影响本机导入记录；没有稳定来源标识，不会自动覆盖。")
        }
    }

    private var shareHelp: some View {
        PageShell(title: "分享导入方法", subtitle: "从备忘录把正文带进来") {
            SurfaceCard {
                VStack(alignment: .leading, spacing: 10) {
                    Text("当前版本还没有系统分享扩展，不能从备忘录直接分享进来。请使用“粘贴并预览”，或了解下面的后续方式。")
                        .font(.bodyText).foregroundStyle(Palette.ink)
                        .fixedSize(horizontal: false, vertical: true)
                    DividerLine()
                    Text("后续方式：在备忘录里选择“分享”或“发送副本”把正文发送到 App，再在预览里确认标题、正文和保存位置。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("如果只收到协作链接，会显示“目前只收到链接，尚未导入正文”，不会把链接当成已读取笔记。")
                        .font(.captionText).foregroundStyle(Palette.muted)
                }
            }
        }
    }

    private var pasteSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text("粘贴内容").font(.title3.weight(.semibold))
                    TextField("标题（可修改）", text: $draftTitle)
                        .accessibilityIdentifier("notes.title")
                        .textFieldStyle(.roundedBorder)
                    Text("正文").font(.captionText).foregroundStyle(Palette.muted)
                    TextEditor(text: $draftBody)
                        .accessibilityIdentifier("notes.body")
                        .frame(minHeight: 180)
                        .padding(6)
                        .background(Palette.card)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    Text("附件：本期只导入正文，附件不会上传或解析。")
                        .font(.captionText).foregroundStyle(Palette.faint)
                    Picker("关联事务", selection: $draftMatter) {
                        Text("不关联").tag(String?.none)
                        ForEach(state.matters) { matter in
                            Text(matter.title).tag(String?.some(matter.title))
                        }
                    }
                    HStack(spacing: 8) {
                        Image(systemName: "cloud").foregroundStyle(Palette.faint)
                        Text("允许云端助手使用：尚未接入").font(.captionText).foregroundStyle(Palette.faint)
                    }
                    PrimaryButton(title: "预览并保存", enabled: !draftBody.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                              identifier: "notes.save") {
                        prepareSave()
                    }
                }
                .padding(Spacing.screen)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { showPaste = false }
                }
            }

        }
    }

    private func prepareSave() {
        if draftTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            draftTitle = String(draftBody.prefix(20))
        }
        let check = notes.checkDuplicate(title: draftTitle, body: draftBody)
        if let exact = check.exact {
            duplicateExact = exact
            showDuplicateDialog = true
        } else if let suspected = check.suspected.first {
            duplicateExact = nil
            showDuplicateDialog = true
            _ = suspected
        } else {
            saveNote(replaceID: nil)
        }
    }

    private func saveNote(replaceID: String?) {
        notes.importNote(title: draftTitle, body: draftBody, matterTitle: draftMatter, replaceID: replaceID)
        showPaste = false
        showDuplicateDialog = false
        draftTitle = ""
        draftBody = ""
        draftMatter = nil
        duplicateExact = nil
    }
}

// MARK: - 共享实例

extension DataSourceStore {
    static let shared = DataSourceStore()
}

extension NoteImportStore {
    static let shared = NoteImportStore()
}
