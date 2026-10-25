import Foundation

@MainActor
final class DataSourceStore: ObservableObject {
    @Published private(set) var selections: [SourceKind: SourceSelection] = [:]
    @Published private(set) var snapshots: [SourceKind: [SourcePreviewItem]] = [:]
    @Published private(set) var lastErrorMessage: [SourceKind: String] = [:]
    @Published private(set) var isRequesting: Set<SourceKind> = []
    @Published private(set) var accountID: String = "guest"
    @Published private(set) var hasUnclaimedLegacy = false

    let provider: DataSourceProviding
    private let now: () -> Date
    private let baseDirectory: URL?
    private var directory: URL
    private var refreshTasks: [SourceKind: Task<Void, Never>] = [:]
    private var generation = 0

    init(provider: DataSourceProviding = EventKitDataSource(),
         directory: URL? = nil,
         rootDirectory: URL? = nil,
         accountID: String? = nil,
         now: @escaping () -> Date = Date.init) {
        self.provider = provider
        self.now = now
        self.accountID = Self.namespace(accountID)
        if let directory {
            self.baseDirectory = nil
            self.directory = directory
        } else {
            let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? FileManager.default.temporaryDirectory
            let root = rootDirectory ?? base.appendingPathComponent("DataSources", isDirectory: true)
            self.baseDirectory = root
            self.directory = root.appendingPathComponent(Self.namespace(accountID), isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
        quarantineLegacyIfNeeded()
        load()
    }

    // MARK: - 账号

    func setAccount(_ raw: String?) {
        let next = Self.namespace(raw)
        guard next != accountID else { return }
        cancelReads()
        generation += 1
        accountID = next
        if let baseDirectory {
            directory = baseDirectory.appendingPathComponent(next, isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        selections = [:]
        snapshots = [:]
        lastErrorMessage = [:]
        isRequesting = []
        quarantineLegacyIfNeeded()
        load()
    }

    static func namespace(_ raw: String?) -> String {
        guard let raw, !raw.isEmpty else { return "guest" }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        return String(raw.unicodeScalars.map { allowed.contains($0) ? Character($0) : "_" })
    }

    // MARK: - 状态

    func selection(_ kind: SourceKind) -> SourceSelection {
        selections[kind] ?? SourceSelection()
    }

    func groups(for kind: SourceKind) -> [SourceGroup] { provider.groups(for: kind) }

    func items(_ kind: SourceKind) -> [SourcePreviewItem] { snapshots[kind] ?? [] }

    func state(for kind: SourceKind) -> SourceConnectionState {
        if kind == .notes { return .notConnected }
        let item = selection(kind)
        if item.paused {
            return .paused(lastReadAt: item.lastReadAt, itemCount: items(kind).count)
        }
        let permission = provider.permission(for: kind)
        switch permission {
        case .denied, .restricted:
            return item.previouslyAuthorized && !item.selectedIDs.isEmpty ? .permissionRevoked : .permissionDenied
        case .writeOnly:
            return .permissionDenied
        case .notDetermined:
            return .notConnected
        case .fullAccess:
            break
        }
        if let message = lastErrorMessage[kind] {
            return .failed(message)
        }
        if item.selectedIDs.isEmpty {
            return .grantedNoSelection
        }
        if item.lastReadAt == nil {
            return .selectedNotRead
        }
        if items(kind).isEmpty {
            return .selectedEmpty
        }
        return .ready(lastReadAt: item.lastReadAt ?? now(), itemCount: items(kind).count)
    }

    // MARK: - 权限与读取

    @discardableResult
    func connect(_ kind: SourceKind) async -> Bool {
        guard !isRequesting.contains(kind) else { return false }
        isRequesting.insert(kind)
        defer { isRequesting.remove(kind) }
        let gen = generation
        do {
            let granted = try await provider.requestAccess(for: kind)
            guard gen == generation else { return false }
            var item = selection(kind)
            item.previouslyAuthorized = granted
            selections[kind] = item
            persist()
            return granted
        } catch {
            guard gen == generation else { return false }
            lastErrorMessage[kind] = "无法请求权限。"
            persist()
            return false
        }
    }

    /// 保存用户明确勾选的范围；不会默认全选。
    func applySelection(_ ids: Set<String>, for kind: SourceKind) {
        var item = selection(kind)
        item.selectedIDs = ids
        item.previouslyAuthorized = item.previouslyAuthorized || provider.permission(for: kind) == .fullAccess
        selections[kind] = item
        persist()
        refresh(kind)
    }

    func refresh(_ kind: SourceKind) {
        guard kind != .notes else { return }
        let ids = selection(kind).selectedIDs
        let gen = generation
        let account = accountID
        refreshTasks[kind]?.cancel()
        guard !ids.isEmpty else {
            snapshots[kind] = []
            persist()
            return
        }
        refreshTasks[kind] = Task { [weak self] in
            guard let self else { return }
            do {
                let preview = try await self.provider.preview(kind: kind, selectedIDs: ids, limit: 20)
                guard !Task.isCancelled else { return }
                guard gen == self.generation, account == self.accountID else { return }
                self.snapshots[kind] = preview
                var updated = self.selection(kind)
                updated.lastReadAt = self.now()
                updated.lastError = nil
                self.selections[kind] = updated
                self.lastErrorMessage[kind] = nil
                self.persist()
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled else { return }
                guard gen == self.generation, account == self.accountID else { return }
                self.lastErrorMessage[kind] = "读取失败：可能是权限被撤回或系统暂不可用。"
                self.persist()
            }
        }
    }

    /// 测试与显式刷新用；会等待本次读取结束。
    func refreshAndWait(_ kind: SourceKind) async {
        refresh(kind)
        await refreshTasks[kind]?.value
    }

    func setPaused(_ paused: Bool, for kind: SourceKind) {
        var item = selection(kind)
        item.paused = paused
        selections[kind] = item
        persist()
        if paused {
            refreshTasks[kind]?.cancel()
        } else {
            refresh(kind)
        }
    }

    /// 只删除本机保存的副本；不影响设备权限，也不声称删除了服务器数据（本期没有上传）。
    func removeLocalCopy(for kind: SourceKind) {
        refreshTasks[kind]?.cancel()
        snapshots[kind] = []
        var item = selection(kind)
        item.lastReadAt = nil
        selections[kind] = item
        lastErrorMessage[kind] = nil
        persist()
    }

    func clearSelection(for kind: SourceKind) {
        refreshTasks[kind]?.cancel()
        var item = selection(kind)
        item.selectedIDs = []
        item.lastReadAt = nil
        selections[kind] = item
        snapshots[kind] = []
        lastErrorMessage[kind] = nil
        persist()
    }

    func lastSuccessfulRead(_ kind: SourceKind) -> Date? { selection(kind).lastReadAt }

    // MARK: - 旧设备数据隔离与认领

    private var legacyFile: URL? { baseDirectory?.appendingPathComponent("sources.json") }
    private var unclaimedFile: URL? {
        baseDirectory?.appendingPathComponent("_legacy-unclaimed", isDirectory: true)
            .appendingPathComponent("sources.json")
    }

    private func quarantineLegacyIfNeeded() {
        guard let baseDirectory, let legacyFile else { return }
        try? FileManager.default.createDirectory(
            at: baseDirectory.appendingPathComponent("_legacy-unclaimed", isDirectory: true),
            withIntermediateDirectories: true
        )
        if FileManager.default.fileExists(atPath: legacyFile.path), let target = unclaimedFile {
            try? FileManager.default.removeItem(at: target)
            try? FileManager.default.moveItem(at: legacyFile, to: target)
        }
        hasUnclaimedLegacy = unclaimedFile.map { FileManager.default.fileExists(atPath: $0.path) } ?? false
    }

    /// 只有当前账号本机还没有数据时才允许认领；认领后旧文件保留备份，不静默删除。
    @discardableResult
    func claimLegacyData() -> Bool {
        guard let baseDirectory,
              let unclaimedFile,
              FileManager.default.fileExists(atPath: unclaimedFile.path),
              !FileManager.default.fileExists(atPath: fileURL.path) else { return false }
        let claimID = "\(accountID)-\(Int(now().timeIntervalSince1970))"
        let archive = baseDirectory
            .appendingPathComponent("_legacy-claimed", isDirectory: true)
            .appendingPathComponent(claimID, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: true)
            // 先原子移出可认领区，避免复制失败时同一批数据被另一个账号再次认领。
            let archived = archive.appendingPathComponent("sources.json")
            try FileManager.default.moveItem(at: unclaimedFile, to: archived)
            try FileManager.default.copyItem(at: archived, to: fileURL)
            let receipt: [String: String] = [
                "account_id": accountID,
                "claim_id": claimID,
                "claimed_at": ISO8601DateFormatter().string(from: now()),
                "original": "sources.json",
            ]
            if let data = try? JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted]) {
                try? data.write(to: archive.appendingPathComponent("receipt.json"), options: .atomic)
            }
            hasUnclaimedLegacy = false
            load()
            return true
        } catch {
            return false
        }
    }

    // MARK: - 持久化

    private struct Payload: Codable {
        var selections: [String: SourceSelection]
        var snapshots: [String: SavedSourceSnapshot]
    }

    private var fileURL: URL { directory.appendingPathComponent("sources.json") }

    private func cancelReads() {
        refreshTasks.values.forEach { $0.cancel() }
        refreshTasks = [:]
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let payload = try? PAJSON.decoder.decode(Payload.self, from: data) else { return }
        for (key, value) in payload.selections {
            if let kind = SourceKind(rawValue: key) { selections[kind] = value }
        }
        for (key, value) in payload.snapshots {
            if let kind = SourceKind(rawValue: key) { snapshots[kind] = value.items }
        }
    }

    private func persist() {
        let payload = Payload(
            selections: Dictionary(uniqueKeysWithValues: selections.map { ($0.key.rawValue, $0.value) }),
            snapshots: Dictionary(uniqueKeysWithValues: snapshots.map {
                ($0.key.rawValue, SavedSourceSnapshot(items: $0.value, savedAt: selection($0.key).lastReadAt ?? now()))
            })
        )
        guard let data = try? PAJSON.encoder.encode(payload) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}

// MARK: - 备忘录导入

@MainActor
final class NoteImportStore: ObservableObject {
    @Published private(set) var notes: [ImportedNote] = []
    @Published private(set) var accountID: String = "guest"
    @Published private(set) var hasUnclaimedLegacy = false
    @Published var errorMessage: String?

    private let now: () -> Date
    private let baseDirectory: URL?
    private var directory: URL

    init(directory: URL? = nil, rootDirectory: URL? = nil,
         accountID: String? = nil, now: @escaping () -> Date = Date.init) {
        self.now = now
        self.accountID = DataSourceStore.namespace(accountID)
        if let directory {
            self.baseDirectory = nil
            self.directory = directory
        } else {
            let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? FileManager.default.temporaryDirectory
            let root = rootDirectory ?? base.appendingPathComponent("NoteImports", isDirectory: true)
            self.baseDirectory = root
            self.directory = root.appendingPathComponent(DataSourceStore.namespace(accountID), isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
        quarantineLegacyIfNeeded()
        load()
    }

    func setAccount(_ raw: String?) {
        let next = DataSourceStore.namespace(raw)
        guard next != accountID else { return }
        accountID = next
        if let baseDirectory {
            directory = baseDirectory.appendingPathComponent(next, isDirectory: true)
        }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        notes = []
        quarantineLegacyIfNeeded()
        load()
    }

    struct DuplicateCheck {
        let exact: ImportedNote?
        let suspected: [ImportedNote]
    }

    func checkDuplicate(title: String, body: String) -> DuplicateCheck {
        let hash = Self.hash(title: title, body: body)
        let exact = notes.first { $0.contentHash == hash }
        let suspected = notes.filter { $0.title == title && $0.contentHash != hash }
        return DuplicateCheck(exact: exact, suspected: suspected)
    }

    @discardableResult
    func importNote(title: String, body: String, source: String = "粘贴内容",
                    matterTitle: String?, replaceID: String? = nil) -> ImportedNote {
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedBody = body.trimmingCharacters(in: .whitespacesAndNewlines)
        let hash = Self.hash(title: trimmedTitle, body: trimmedBody)
        if let replaceID, let index = notes.firstIndex(where: { $0.id == replaceID }) {
            var note = notes[index]
            note.title = trimmedTitle
            note.body = trimmedBody
            note.matterTitle = matterTitle
            note.importedAt = now()
            note.contentHash = hash
            notes[index] = note
            persist()
            return note
        }
        let note = ImportedNote(
            id: UUID().uuidString,
            title: trimmedTitle,
            body: trimmedBody,
            source: source,
            matterTitle: matterTitle,
            cloudAssistantAllowed: false,
            importedAt: now(),
            contentHash: hash
        )
        notes.insert(note, at: 0)
        persist()
        return note
    }

    func delete(_ id: String) {
        notes.removeAll { $0.id == id }
        persist()
    }

    private var legacyFile: URL? { baseDirectory?.appendingPathComponent("notes.json") }
    private var unclaimedFile: URL? {
        baseDirectory?.appendingPathComponent("_legacy-unclaimed", isDirectory: true)
            .appendingPathComponent("notes.json")
    }

    private func quarantineLegacyIfNeeded() {
        guard let baseDirectory, let legacyFile else { return }
        try? FileManager.default.createDirectory(
            at: baseDirectory.appendingPathComponent("_legacy-unclaimed", isDirectory: true),
            withIntermediateDirectories: true
        )
        if FileManager.default.fileExists(atPath: legacyFile.path), let target = unclaimedFile {
            try? FileManager.default.removeItem(at: target)
            try? FileManager.default.moveItem(at: legacyFile, to: target)
        }
        hasUnclaimedLegacy = unclaimedFile.map { FileManager.default.fileExists(atPath: $0.path) } ?? false
    }

    @discardableResult
    func claimLegacyData() -> Bool {
        guard let baseDirectory,
              let unclaimedFile,
              FileManager.default.fileExists(atPath: unclaimedFile.path),
              !FileManager.default.fileExists(atPath: fileURL.path) else { return false }
        let claimID = "\(accountID)-\(Int(now().timeIntervalSince1970))"
        let archive = baseDirectory
            .appendingPathComponent("_legacy-claimed", isDirectory: true)
            .appendingPathComponent(claimID, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: true)
            let archived = archive.appendingPathComponent("notes.json")
            try FileManager.default.moveItem(at: unclaimedFile, to: archived)
            try FileManager.default.copyItem(at: archived, to: fileURL)
            let receipt: [String: String] = [
                "account_id": accountID,
                "claim_id": claimID,
                "claimed_at": ISO8601DateFormatter().string(from: now()),
                "original": "notes.json",
            ]
            if let data = try? JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted]) {
                try? data.write(to: archive.appendingPathComponent("receipt.json"), options: .atomic)
            }
            hasUnclaimedLegacy = false
            load()
            return true
        } catch {
            return false
        }
    }

    private var fileURL: URL { directory.appendingPathComponent("notes.json") }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? PAJSON.decoder.decode([ImportedNote].self, from: data) else { return }
        notes = decoded
    }

    private func persist() {
        guard let data = try? PAJSON.encoder.encode(notes) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    private static func hash(title: String, body: String) -> String {
        (title + "\u{1F}" + body).data(using: .utf8)?.base64EncodedString() ?? ""
    }
}
