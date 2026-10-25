import SwiftUI

/// Real PA data-source/content console. Local previews only become cloud rows
/// after an explicit upload; no demo fallback lives here.
struct CloudSyncView: View {
    @StateObject private var local = DataSourceStore.shared
    @StateObject private var notes = NoteImportStore.shared
    private let api = CloudSyncClient()

    @State private var sources: [CloudSourceRead] = []
    @State private var contents: [CloudContentRead] = []
    @State private var selected: CloudSourceRead?
    @State private var detail: CloudContentRead?
    @State private var loading = false
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?
    @State private var showCreate = false
    @State private var showContent = false
    @State private var query = ""
    @State private var contentKind = ""

    var body: some View {
        NavigationStack {
            List {
                if let error {
                    Section { Text(error).foregroundStyle(.red).font(.caption) }
                }
                if let notice {
                    Section { Text(notice).font(.caption).foregroundStyle(Palette.fogDeep) }
                }
                Section("云端来源") {
                    if sources.isEmpty && !loading {
                        Text("还没有已上传的来源。先在本机选择日历/提醒，或在备忘录里粘贴正文，再创建来源。")
                            .font(.caption).foregroundStyle(Palette.muted)
                    }
                    ForEach(sources) { source in
                        Button { selected = source } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(source.displayName).font(.body.weight(.semibold))
                                    Spacer()
                                    Text(source.syncEnabled ? "同步开" : "同步停")
                                        .font(.caption).foregroundStyle(source.syncEnabled ? Palette.fogDeep : Palette.amber)
                                }
                                Text("\(source.sourceType) · \(source.scopeKeys.count) 个范围 · \(source.itemCount) 条副本 · 助手\(source.assistantEnabled ? "可用" : "关闭")")
                                    .font(.caption).foregroundStyle(Palette.muted)
                                if let at = source.lastSyncAt {
                                    Text("最近同步 \(at.formatted(date: .abbreviated, time: .shortened)) · \(source.lastSyncStatus ?? "")")
                                        .font(.caption2).foregroundStyle(Palette.faint)
                                }
                                if let code = source.lastErrorCode {
                                    Text("错误：\(code)").font(.caption2).foregroundStyle(Palette.amber)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                Section("已上传内容") {
                    Button("查看内容与详情") { showContent = true }
                        .accessibilityIdentifier("cloud.content")
                }
                Section {
                    Button("新建来源") { showCreate = true }
                        .accessibilityIdentifier("cloud.create")
                    Button("刷新列表") { Task { await load() } }
                        .disabled(loading)
                }
            }
            .navigationTitle("云端同步")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() } } }
            .overlay { if loading && sources.isEmpty { ProgressView() } }
            .task {
                await load()
                if ProcessInfo.processInfo.environment["START_CLOUD_CONTENT"] == "1" {
                    try? await Task.sleep(nanoseconds: 700_000_000)
                    showContent = true
                }
            }
            .sheet(isPresented: $showCreate) {
                CreateCloudSourceView(local: local, notes: notes) { await load() }
            }
            .sheet(item: $selected) { source in
                CloudSourceDetailView(source: source, local: local, notes: notes) {
                    Task { await load() }
                }
            }
            .sheet(isPresented: $showContent) {
                CloudContentView(api: api)
            }
        }
    }

    @Environment(\.dismiss) private var dismiss

    private func load() async {
        loading = true; error = nil
        do { sources = try await api.listSources() }
        catch { self.error = "读取云端来源失败：\(error.localizedDescription)" }
        loading = false
    }
}

private struct CreateCloudSourceView: View {
    @ObservedObject var local: DataSourceStore
    @ObservedObject var notes: NoteImportStore
    let onCreated: () async -> Void
    @Environment(\.dismiss) private var dismiss
    private let api = CloudSyncClient()

    @State private var kind: SourceKind = .calendar
    @State private var displayName = "iPhone 日历"
    @State private var scopeText = ""
    @State private var fields: Set<String> = []
    @State private var busy = false
    @State private var error: String?

    private var allFields: [String] {
        switch kind {
        case .calendar: return ["title", "start", "end", "location", "notes", "status"]
        case .reminders: return ["title", "due", "notes", "status"]
        case .notes: return ["title", "body", "tags"]
        }
    }

    private var defaultScopes: [String] {
        if kind == .notes { return ["notes"] }
        let ids = Array(local.selection(kind).selectedIDs)
        return ids.isEmpty ? [] : ids
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("来源") {
                    Picker("类型", selection: $kind) {
                        Text("日历").tag(SourceKind.calendar)
                        Text("提醒事项").tag(SourceKind.reminders)
                        Text("备忘录").tag(SourceKind.notes)
                    }
                    .pickerStyle(.segmented)
                    .accessibilityIdentifier("cloud.kind")
                    TextField("显示名称", text: $displayName)
                }
                Section("范围 scope_keys") {
                    TextField("用逗号分隔；默认使用本机已选范围", text: $scopeText, axis: .vertical)
                        .lineLimit(2...5)
                    Button("使用本机已选范围") {
                        scopeText = defaultScopes.joined(separator: ",")
                    }
                    Text(kind == .notes ? "备忘录默认使用 notes 范围。" : "只会上传当前选择范围内的内容。")
                        .font(.caption).foregroundStyle(Palette.muted)
                }
                Section("允许上传字段") {
                    ForEach(allFields, id: \.self) { field in
                        Toggle(field, isOn: Binding(
                            get: { fields.contains(field) },
                            set: { fields = $0 ? fields.union([field]) : fields.subtracting([field]) }
                        ))
                    }
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }
            .navigationTitle("新建云端来源")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("创建") { Task { await create() } }.disabled(busy)
                        .accessibilityIdentifier("cloud.create.submit")
                }
            }
            .onAppear {
                fields = Set(kind == .notes ? ["title", "body"] : kind == .calendar ? ["title", "start", "end", "location"] : ["title", "due", "status"])
                scopeText = defaultScopes.joined(separator: ",")
            }
            .onChange(of: kind) { _, new in
                displayName = new == .calendar ? "iPhone 日历" : new == .reminders ? "iPhone 提醒事项" : "粘贴备忘录"
                fields = Set(new == .notes ? ["title", "body"] : new == .calendar ? ["title", "start", "end", "location"] : ["title", "due", "status"])
                scopeText = defaultScopes.joined(separator: ",")
            }
        }
    }

    private func create() async {
        let scopes = scopeText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard !scopes.isEmpty else { error = "至少需要一个 scope。"; return }
        guard fields.contains("title") else { error = "title 必须授权。"; return }
        busy = true; error = nil
        do {
            _ = try await api.createSource(type: kind.rawValue, displayName: displayName,
                                           scopeKeys: scopes, fields: Array(fields).sorted())
            await onCreated(); dismiss()
        } catch { self.error = error.localizedDescription }
        busy = false
    }
}

private struct CloudSourceDetailView: View {
    let source: CloudSourceRead
    @ObservedObject var local: DataSourceStore
    @ObservedObject var notes: NoteImportStore
    let onChanged: () -> Void
    @Environment(\.dismiss) private var dismiss
    private let api = CloudSyncClient()

    @State private var syncEnabled: Bool
    @State private var assistantEnabled: Bool
    @State private var scopeText: String
    @State private var fields: Set<String>
    @State private var current: CloudSourceRead
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?

    init(source: CloudSourceRead, local: DataSourceStore, notes: NoteImportStore, onChanged: @escaping () -> Void) {
        self.source = source; self.local = local; self.notes = notes; self.onChanged = onChanged
        _current = State(initialValue: source)
        _syncEnabled = State(initialValue: source.syncEnabled)
        _assistantEnabled = State(initialValue: source.assistantEnabled)
        _scopeText = State(initialValue: source.scopeKeys.joined(separator: ","))
        _fields = State(initialValue: Set(source.uploadFields))
    }

    private var allFields: [String] {
        switch current.sourceType {
        case "calendar": return ["title", "start", "end", "location", "notes", "status"]
        case "reminders": return ["title", "due", "notes", "status"]
        default: return ["title", "body", "tags"]
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("授权") {
                    Toggle("允许后续上传", isOn: $syncEnabled)
                    Toggle("允许云端助手读取", isOn: $assistantEnabled)
                    Text("关闭上传不会删除已有副本；关闭助手只影响工具读取。")
                        .font(.caption).foregroundStyle(Palette.muted)
                }
                Section("范围") {
                    TextField("scope_keys，逗号分隔", text: $scopeText, axis: .vertical).lineLimit(2...5)
                }
                Section("字段") {
                    ForEach(allFields, id: \.self) { field in
                        Toggle(field, isOn: Binding(
                            get: { fields.contains(field) },
                            set: { fields = $0 ? fields.union([field]) : fields.subtracting([field]) }
                        ))
                    }
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
                if let notice { Section { Text(notice).font(.caption) } }
                Section("操作") {
                    Button("保存设置") { Task { await save() } }.disabled(busy)
                        .accessibilityIdentifier("cloud.save")
                    Button("上传当前本机预览") { Task { await upload() } }.disabled(busy || !syncEnabled)
                        .accessibilityIdentifier("cloud.upload")
                    Button("删除已上传副本", role: .destructive) { Task { await deleteItems() } }.disabled(busy)
                    Button("删除来源", role: .destructive) { Task { await deleteSource() } }.disabled(busy)
                }
                Section {
                    Text("本机条目：\(localItems.count)").font(.caption).foregroundStyle(Palette.muted)
                    Text("删除副本保留来源和授权；删除来源会连同来源一起删除。")
                        .font(.caption2).foregroundStyle(Palette.faint)
                }
            }
            .navigationTitle(current.displayName)
            .task {
                if let refreshed = try? await api.source(id: source.sourceId) { current = refreshed }
            }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() } } }
        }
    }

    private var localItems: [SourcePreviewItem] {
        switch current.sourceType {
        case "calendar": return local.items(.calendar)
        case "reminders": return local.items(.reminders)
        default: return notes.notes.map {
            SourcePreviewItem(id: $0.contentHash, title: $0.title, subtitle: $0.source, date: $0.importedAt,
                              scopeKey: "notes", notes: $0.body)
        }
        }
    }

    private func save() async {
        busy = true; error = nil
        do {
            let scopes = scopeText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            var patch = CloudSyncClient.SourcePatch()
            patch.sync_enabled = syncEnabled
            patch.assistant_enabled = assistantEnabled
            patch.upload_fields = Array(fields).sorted()
            patch.scope_keys = scopes
            _ = try await api.updateSource(id: source.sourceId, patch: patch)
            notice = "后端已确认保存。"
            onChanged()
        } catch { self.error = error.localizedDescription }
        busy = false
    }

    private func upload() async {
        busy = true; error = nil; notice = nil
        let allowed = fields
        let scopes = Set(current.scopeKeys)
        let items: [CloudSyncItem] = localItems.compactMap { item in
            guard let scope = item.scopeKey ?? scopes.first, scopes.isEmpty || scopes.contains(scope) else { return nil }
            let now = Date()
            switch current.sourceType {
            case "calendar":
                return CloudSyncItem(externalId: item.id, scopeKey: scope, version: 1,
                                     updatedAt: item.date ?? now, title: allowed.contains("title") ? item.title : nil,
                                     body: nil, notes: allowed.contains("notes") ? item.notes : nil,
                                     startsAt: allowed.contains("start") ? item.date : nil,
                                     endsAt: allowed.contains("end") ? item.endDate : nil,
                                     dueAt: nil, location: allowed.contains("location") ? item.location : nil,
                                     status: allowed.contains("status") ? item.status : nil, tags: nil)
            case "reminders":
                return CloudSyncItem(externalId: item.id, scopeKey: scope, version: 1,
                                     updatedAt: now, title: allowed.contains("title") ? item.title : nil,
                                     body: nil, notes: allowed.contains("notes") ? item.notes : nil,
                                     startsAt: nil, endsAt: nil,
                                     dueAt: allowed.contains("due") ? item.date : nil,
                                     location: nil, status: allowed.contains("status") ? item.status : nil, tags: nil)
            default:
                return CloudSyncItem(externalId: item.id, scopeKey: "notes", version: 1,
                                     updatedAt: item.date ?? now, title: allowed.contains("title") ? item.title : nil,
                                     body: allowed.contains("body") ? item.notes : nil, notes: nil,
                                     startsAt: nil, endsAt: nil, dueAt: nil, location: nil, status: nil, tags: nil)
            }
        }
        guard !items.isEmpty else { error = "没有可上传的本机预览。请先在来源页选择范围并读取。"; busy = false; return }
        do {
            let result = try await api.sync(sourceId: source.sourceId, batchID: UUID().uuidString, items: items)
            notice = "同步完成：新增 \(result.created)，更新 \(result.updated)，未变 \(result.unchanged)。"
            onChanged()
        } catch { self.error = error.localizedDescription }
        busy = false
    }

    private func deleteItems() async {
        busy = true; error = nil
        do { try await api.deleteItems(sourceId: source.sourceId); notice = "已删除该来源的已上传副本，来源保留。"; onChanged() }
        catch { self.error = error.localizedDescription }
        busy = false
    }

    private func deleteSource() async {
        busy = true; error = nil
        do { try await api.deleteSource(id: source.sourceId); onChanged(); dismiss() }
        catch { self.error = error.localizedDescription }
        busy = false
    }
}

private struct CloudContentView: View {
    let api: CloudSyncClient
    @Environment(\.dismiss) private var dismiss
    @State private var items: [CloudContentRead] = []
    @State private var query = ""
    @State private var kind = ""
    @State private var selected: CloudContentRead?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        NavigationStack {
            List {
                if let error { Section { Text(error).foregroundStyle(.red) } }
                ForEach(items) { item in
                    Button { selected = item } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.title).font(.body.weight(.semibold))
                            Text("\(item.sourceDisplayName) · \(item.kind) · \(item.syncedAt.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption).foregroundStyle(Palette.muted)
                        }
                    }
                    .buttonStyle(.plain)
                }
                if items.isEmpty && !loading { Section { Text("没有可显示的已上传内容。").font(.caption) } }
            }
            .searchable(text: $query, prompt: "搜索标题/正文")
            .onSubmit(of: .search) { Task { await load() } }
            .navigationTitle("已上传内容")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("关闭") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("刷新") { Task { await load() } } }
            }
            .task { await load() }
            .sheet(item: $selected) { item in
                NavigationStack { ScrollView { VStack(alignment: .leading, spacing: 12) {
                    Text(item.title).font(.title3.weight(.semibold))
                    Text("来源：\(item.sourceDisplayName)").font(.caption)
                    if !item.body.isEmpty { Text(item.body) }
                    if let at = item.dueAt { Text("截止：\(at.formatted())").font(.caption) }
                    if let at = item.startsAt { Text("开始：\(at.formatted())").font(.caption) }
                    if let at = item.endsAt { Text("结束：\(at.formatted())").font(.caption) }
                    if let location = item.location { Text("地点：\(location)").font(.caption) }
                    Text("external_id：\(item.externalId)").font(.caption2).foregroundStyle(Palette.faint)
                }.padding() } .navigationTitle("内容详情")
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button("关闭") { self.selected = nil } } }
                }
            }
        }
    }

    private func load() async {
        loading = true; error = nil
        do {
            items = try await api.content(kind: kind.isEmpty ? nil : kind, query: query).items
            if ProcessInfo.processInfo.environment["START_CLOUD_ITEM"] == "1", let first = items.first {
                selected = try? await api.contentItem(id: first.itemId)
            }
        }
        catch { self.error = error.localizedDescription }
        loading = false
    }
}
