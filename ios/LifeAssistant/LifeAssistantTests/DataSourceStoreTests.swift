import XCTest
@testable import LifeAssistant

@MainActor
final class DataSourceStoreTests: XCTestCase {
    private var root: URL!
    private var provider: FakeDataSourceProvider!
    private let fixedNow = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() {
        super.setUp()
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        provider = FakeDataSourceProvider()
        provider.groups = [
            .calendar: [
                SourceGroup(id: "iCloud", title: "iCloud", sources: [
                    SelectableSource(id: "cal-work", title: "工作", subtitle: "iCloud", account: "iCloud", itemCount: 0),
                    SelectableSource(id: "cal-personal", title: "个人", subtitle: "iCloud", account: "iCloud", itemCount: 0),
                ])
            ]
        ]
        provider.items = [
            .calendar: ["cal-work": [
                SourcePreviewItem(id: "e1", title: "评审", subtitle: "工作", date: fixedNow)
            ]]
        ]
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        super.tearDown()
    }

    private func makeStore(account: String = "user-a") -> DataSourceStore {
        DataSourceStore(provider: provider, rootDirectory: root, accountID: account, now: { self.fixedNow })
    }

    func testNotDeterminedIsNotConnected() {
        provider.permissions = [.calendar: .notDetermined]
        XCTAssertEqual(makeStore().state(for: .calendar), .notConnected)
    }

    func testDeniedIsPermissionDeniedNotEmptyContent() {
        provider.permissions = [.calendar: .denied]
        XCTAssertEqual(makeStore().state(for: .calendar), .permissionDenied)
    }

    func testGrantedWithoutSelectionIsDistinctState() {
        provider.permissions = [.calendar: .fullAccess]
        XCTAssertEqual(makeStore().state(for: .calendar), .grantedNoSelection)
    }

    func testSelectionWithItemsBecomesReadyAndStoresLastRead() async {
        let store = makeStore()
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        XCTAssertEqual(store.items(.calendar).count, 1)
        XCTAssertEqual(store.lastSuccessfulRead(.calendar), fixedNow)
        XCTAssertEqual(store.state(for: .calendar), .ready(lastReadAt: fixedNow, itemCount: 1))
    }

    func testEmptySelectionRangeIsDifferentFromFailure() async {
        let store = makeStore()
        store.applySelection(["cal-personal"], for: .calendar)
        await store.refreshAndWait(.calendar)
        XCTAssertEqual(store.state(for: .calendar), .selectedEmpty)
    }

    func testReadFailureKeepsFailureState() async {
        provider.failingKinds = [.calendar]
        let store = makeStore()
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        guard case .failed = store.state(for: .calendar) else {
            return XCTFail("读取失败必须显示失败，不能显示为空")
        }
    }

    func testRevokedPermissionIsDistinctFromNeverGranted() async {
        let store = makeStore()
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        provider.permissions = [.calendar: .denied]
        XCTAssertEqual(store.state(for: .calendar), .permissionRevoked)
    }

    func testPauseKeepsLocalCopyAndResumeRefreshes() async {
        let store = makeStore()
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        store.setPaused(true, for: .calendar)
        XCTAssertEqual(store.items(.calendar).count, 1)
        if case .paused(_, let count) = store.state(for: .calendar) {
            XCTAssertEqual(count, 1)
        } else {
            XCTFail("暂停状态应保留副本")
        }
        store.setPaused(false, for: .calendar)
        await store.refreshAndWait(.calendar)
        XCTAssertEqual(store.state(for: .calendar), .ready(lastReadAt: fixedNow, itemCount: 1))
    }

    func testRemoveLocalCopyKeepsSelectionAndPermission() async {
        let store = makeStore()
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        store.removeLocalCopy(for: .calendar)
        XCTAssertTrue(store.items(.calendar).isEmpty)
        XCTAssertTrue(store.selection(.calendar).selectedIDs.contains("cal-work"))
        XCTAssertEqual(store.state(for: .calendar), .selectedNotRead)
    }

    func testPersistenceRestoresSelectionAndSnapshot() async {
        let store = makeStore()
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        let restored = makeStore()
        XCTAssertEqual(restored.selection(.calendar).selectedIDs, ["cal-work"])
        XCTAssertEqual(restored.items(.calendar).count, 1)
    }

    func testAccountSwitchClearsAndRestoresOnlyThatAccountsData() async {
        let store = makeStore(account: "user-a")
        store.applySelection(["cal-work"], for: .calendar)
        await store.refreshAndWait(.calendar)
        XCTAssertEqual(store.items(.calendar).count, 1)

        store.setAccount("user-b")
        XCTAssertEqual(store.accountID, "user-b")
        XCTAssertTrue(store.items(.calendar).isEmpty)
        XCTAssertTrue(store.selection(.calendar).selectedIDs.isEmpty)

        store.setAccount("user-a")
        XCTAssertEqual(store.items(.calendar).count, 1)
        XCTAssertEqual(store.selection(.calendar).selectedIDs, ["cal-work"])
    }

    func testLateReadFromPreviousAccountCannotOverwriteNewAccount() async {
        provider.previewDelayNanoseconds = 300_000_000
        let store = makeStore(account: "user-a")
        store.applySelection(["cal-work"], for: .calendar)
        store.setAccount("user-b")

        try? await Task.sleep(nanoseconds: 500_000_000)
        XCTAssertEqual(store.accountID, "user-b")
        XCTAssertTrue(store.items(.calendar).isEmpty, "A 的迟到读取不能写进 B")
        XCTAssertTrue(store.selection(.calendar).selectedIDs.isEmpty)
    }

    func testLegacyDataIsQuarantinedThenClaimedExplicitly() throws {
        let legacy = """
        {"selections":{"calendar":{"selectedIDs":["cal-work"],"paused":false,
          "cloudAssistantAllowed":false,"previouslyAuthorized":true,
          "lastReadAt":"2026-10-23T00:00:00Z"}},
         "snapshots":{"calendar":{"items":[
           {"id":"legacy-1","title":"旧日程","subtitle":"工作","date":"2026-10-23T00:00:00Z"}],
           "savedAt":"2026-10-23T00:00:00Z"}}}
        """
        try Data(legacy.utf8).write(to: root.appendingPathComponent("sources.json"))

        let store = makeStore(account: "user-a")
        XCTAssertTrue(store.hasUnclaimedLegacy)
        XCTAssertTrue(store.items(.calendar).isEmpty, "未认领前不得自动展示")

        XCTAssertTrue(store.claimLegacyData())
        XCTAssertFalse(store.hasUnclaimedLegacy)
        XCTAssertEqual(store.items(.calendar).count, 1)
        XCTAssertEqual(store.selection(.calendar).selectedIDs, ["cal-work"])

        // 旧文件必须移出可认领区；归档与凭证保留，但不能再被认领。
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: root.appendingPathComponent("_legacy-unclaimed/sources.json").path))
        let archives = try FileManager.default.contentsOfDirectory(
            at: root.appendingPathComponent("_legacy-claimed"),
            includingPropertiesForKeys: nil)
        XCTAssertEqual(archives.count, 1)
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: archives[0].appendingPathComponent("sources.json").path))
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: archives[0].appendingPathComponent("receipt.json").path))

        let secondAccount = makeStore(account: "user-b")
        XCTAssertFalse(secondAccount.hasUnclaimedLegacy)
        XCTAssertFalse(secondAccount.claimLegacyData(), "B 不能认领 A 已认领的同一批数据")
        XCTAssertTrue(secondAccount.items(.calendar).isEmpty)

        // A 再次启动仍能读到自己认领的数据。
        let restored = makeStore(account: "user-a")
        XCTAssertEqual(restored.items(.calendar).count, 1)
    }

    func testClaimIsRefusedWhenCurrentAccountAlreadyHasData() throws {
        let unclaimed = root.appendingPathComponent("_legacy-unclaimed", isDirectory: true)
        try FileManager.default.createDirectory(at: unclaimed, withIntermediateDirectories: true)
        try Data("{\"selections\":{},\"snapshots\":{}}".utf8)
            .write(to: unclaimed.appendingPathComponent("sources.json"))
        let store = makeStore(account: "user-a")
        store.applySelection([], for: .calendar)
        // 生成当前账号自己的文件。
        store.setPaused(false, for: .calendar)
        XCTAssertFalse(store.claimLegacyData())
    }
}

@MainActor
final class NoteImportStoreTests: XCTestCase {
    private var root: URL!

    override func setUp() {
        super.setUp()
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        super.tearDown()
    }

    func testImportedNoteDoesNotAllowCloudByDefault() {
        let store = NoteImportStore(rootDirectory: root, accountID: "user-a")
        let note = store.importNote(title: "会议结论", body: "下周确认预算", matterTitle: "项目")
        XCTAssertFalse(note.cloudAssistantAllowed)
        XCTAssertEqual(store.notes.count, 1)
    }

    func testExactDuplicateOffersReplaceWithoutAutoOverwrite() {
        let store = NoteImportStore(rootDirectory: root, accountID: "user-a")
        let first = store.importNote(title: "会议结论", body: "下周确认预算", matterTitle: nil)
        let duplicate = store.checkDuplicate(title: "会议结论", body: "下周确认预算")
        XCTAssertEqual(duplicate.exact?.id, first.id)

        let replaced = store.importNote(title: "会议结论", body: "下周确认预算", matterTitle: "项目", replaceID: first.id)
        XCTAssertEqual(store.notes.count, 1)
        XCTAssertEqual(replaced.matterTitle, "项目")
    }

    func testSameTitleDifferentBodyIsOnlySuspected() {
        let store = NoteImportStore(rootDirectory: root, accountID: "user-a")
        _ = store.importNote(title: "会议结论", body: "旧的结论", matterTitle: nil)
        let duplicate = store.checkDuplicate(title: "会议结论", body: "新的结论")
        XCTAssertNil(duplicate.exact)
        XCTAssertEqual(duplicate.suspected.count, 1)
    }

    func testDeleteRemovesOnlyLocalCopy() {
        let store = NoteImportStore(rootDirectory: root, accountID: "user-a")
        let note = store.importNote(title: "会议结论", body: "内容", matterTitle: nil)
        store.delete(note.id)
        XCTAssertTrue(store.notes.isEmpty)
    }

    func testImportsPersist() {
        let store = NoteImportStore(rootDirectory: root, accountID: "user-a")
        _ = store.importNote(title: "会议结论", body: "内容", matterTitle: nil)
        let restored = NoteImportStore(rootDirectory: root, accountID: "user-a")
        XCTAssertEqual(restored.notes.count, 1)
    }

    func testLegacyNotesClaimIsOneTime() throws {
        let legacy = """
        [{"id":"old-note","title":"旧笔记","body":"内容","source":"粘贴内容",
          "matterTitle":null,"cloudAssistantAllowed":false,
          "importedAt":"2026-10-23T00:00:00Z","contentHash":"hash-old"}]
        """
        try Data(legacy.utf8).write(to: root.appendingPathComponent("notes.json"))

        let a = NoteImportStore(rootDirectory: root, accountID: "user-a")
        XCTAssertTrue(a.hasUnclaimedLegacy)
        XCTAssertTrue(a.claimLegacyData())
        XCTAssertEqual(a.notes.count, 1)

        let b = NoteImportStore(rootDirectory: root, accountID: "user-b")
        XCTAssertFalse(b.hasUnclaimedLegacy)
        XCTAssertFalse(b.claimLegacyData(), "B 不能认领 A 已认领的旧笔记")
        XCTAssertTrue(b.notes.isEmpty)
    }

    func testNotesAreIsolatedBetweenAccountsAndGuest() {
        let store = NoteImportStore(rootDirectory: root, accountID: "user-a")
        _ = store.importNote(title: "A 的笔记", body: "内容", matterTitle: nil)

        store.setAccount("user-b")
        XCTAssertTrue(store.notes.isEmpty)

        store.setAccount(nil)
        XCTAssertEqual(store.accountID, "guest")
        XCTAssertTrue(store.notes.isEmpty)

        store.setAccount("user-a")
        XCTAssertEqual(store.notes.count, 1)
        XCTAssertEqual(store.notes.first?.title, "A 的笔记")
    }
}
