import Foundation
import EventKit

protocol DataSourceProviding {
    func permission(for kind: SourceKind) -> SourcePermission
    @discardableResult
    func requestAccess(for kind: SourceKind) async throws -> Bool
    func groups(for kind: SourceKind) -> [SourceGroup]
    func preview(kind: SourceKind, selectedIDs: Set<String>, limit: Int) async throws -> [SourcePreviewItem]
}

enum SourceReadError: LocalizedError {
    case permissionDenied
    case readFailed
    case unsupported

    var errorDescription: String? {
        switch self {
        case .permissionDenied: return "没有设备读取权限。"
        case .readFailed: return "读取失败，稍后可以重试。"
        case .unsupported: return "当前设备不支持这个来源。"
        }
    }
}

struct EventKitDataSource: DataSourceProviding {
    private let store = EKEventStore()

    func permission(for kind: SourceKind) -> SourcePermission {
        let status: EKAuthorizationStatus
        switch kind {
        case .calendar: status = EKEventStore.authorizationStatus(for: .event)
        case .reminders: status = EKEventStore.authorizationStatus(for: .reminder)
        case .notes: return .notDetermined
        }
        switch status {
        case .fullAccess: return .fullAccess
        case .writeOnly: return .writeOnly
        case .denied: return .denied
        case .restricted: return .restricted
        case .notDetermined: return .notDetermined
        @unknown default: return .notDetermined
        }
    }

    @discardableResult
    func requestAccess(for kind: SourceKind) async throws -> Bool {
        do {
            switch kind {
            case .calendar:
                return try await store.requestFullAccessToEvents()
            case .reminders:
                return try await store.requestFullAccessToReminders()
            case .notes:
                throw SourceReadError.unsupported
            }
        } catch {
            throw SourceReadError.permissionDenied
        }
    }

    func groups(for kind: SourceKind) -> [SourceGroup] {
        let entity: EKEntityType = kind == .calendar ? .event : .reminder
        let calendars = store.calendars(for: entity)
        let grouped = Dictionary(grouping: calendars) { $0.source?.title ?? "其他账户" }
        return grouped.keys.sorted().map { account in
            let items = (grouped[account] ?? []).sorted { $0.title < $1.title }.map { calendar in
                SelectableSource(
                    id: calendar.calendarIdentifier,
                    title: calendar.title,
                    subtitle: kind == .calendar ? (calendar.source?.title ?? "") : "未完成项",
                    account: account,
                    itemCount: 0
                )
            }
            return SourceGroup(id: account, title: account, sources: items)
        }
    }

    func preview(kind: SourceKind, selectedIDs: Set<String>, limit: Int) async throws -> [SourcePreviewItem] {
        guard !selectedIDs.isEmpty else { return [] }
        switch kind {
        case .calendar:
            let calendars = store.calendars(for: .event).filter { selectedIDs.contains($0.calendarIdentifier) }
            guard !calendars.isEmpty else { return [] }
            let start = Calendar.current.date(byAdding: .day, value: -7, to: Date()) ?? Date()
            let end = Calendar.current.date(byAdding: .day, value: 30, to: Date()) ?? Date()
            let predicate = store.predicateForEvents(withStart: start, end: end, calendars: calendars)
            return store.events(matching: predicate)
                .sorted { $0.startDate < $1.startDate }
                .prefix(limit)
                .map { event in
                    SourcePreviewItem(
                        id: event.eventIdentifier ?? UUID().uuidString,
                        title: event.title ?? "未命名日程",
                        subtitle: event.calendar.title,
                        date: event.startDate,
                        scopeKey: event.calendar.calendarIdentifier,
                        endDate: event.endDate,
                        location: event.location,
                        notes: event.notes,
                        status: event.status == .canceled ? "cancelled" : "scheduled"
                    )
                }
        case .reminders:
            let calendars = store.calendars(for: .reminder).filter { selectedIDs.contains($0.calendarIdentifier) }
            guard !calendars.isEmpty else { return [] }
            let predicate = store.predicateForReminders(in: calendars)
            let reminders: [EKReminder] = try await fetchReminders(predicate: predicate)
            return reminders
                .filter { !$0.isCompleted }
                .sorted { ($0.dueDateComponents?.date ?? .distantFuture) < ($1.dueDateComponents?.date ?? .distantFuture) }
                .prefix(limit)
                .map { reminder in
                    SourcePreviewItem(
                        id: reminder.calendarItemIdentifier,
                        title: reminder.title ?? "未命名提醒",
                        subtitle: reminder.calendar.title,
                        date: reminder.dueDateComponents?.date,
                        scopeKey: reminder.calendar.calendarIdentifier,
                        notes: reminder.notes,
                        status: reminder.isCompleted ? "completed" : "open"
                    )
                }
        case .notes:
            throw SourceReadError.unsupported
        }
    }
}

private extension EventKitDataSource {
    /// 事件回调与超时可能同时到达；用一次性闸门保证只 resume 一次，不阻塞主线程。
    final class OneShot: @unchecked Sendable {
        private let lock = NSLock()
        private var finished = false
        func claim() -> Bool {
            lock.lock(); defer { lock.unlock() }
            if finished { return false }
            finished = true
            return true
        }
    }

    func fetchReminders(predicate: NSPredicate) async throws -> [EKReminder] {
        let store = self.store
        let gate = OneShot()
        return try await withCheckedThrowingContinuation { continuation in
            store.fetchReminders(matching: predicate) { reminders in
                guard gate.claim() else { return }
                continuation.resume(returning: reminders ?? [])
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + 6) {
                guard gate.claim() else { return }
                continuation.resume(throwing: SourceReadError.readFailed)
            }
        }
    }
}

// MARK: - 测试 / 预览替身

final class FakeDataSourceProvider: DataSourceProviding {
    var permissions: [SourceKind: SourcePermission] = [.calendar: .fullAccess, .reminders: .fullAccess]
    var groups: [SourceKind: [SourceGroup]] = [:]
    var items: [SourceKind: [String: [SourcePreviewItem]]] = [:]
    var failingKinds: Set<SourceKind> = []
    var requestResult = true

    func permission(for kind: SourceKind) -> SourcePermission { permissions[kind] ?? .notDetermined }

    @discardableResult
    func requestAccess(for kind: SourceKind) async throws -> Bool {
        if requestResult {
            permissions[kind] = .fullAccess
        } else {
            permissions[kind] = .denied
        }
        return requestResult
    }

    func groups(for kind: SourceKind) -> [SourceGroup] { groups[kind] ?? [] }

    var previewDelayNanoseconds: UInt64 = 0

    func preview(kind: SourceKind, selectedIDs: Set<String>, limit: Int) async throws -> [SourcePreviewItem] {
        if previewDelayNanoseconds > 0 {
            try await Task.sleep(nanoseconds: previewDelayNanoseconds)
        }
        if failingKinds.contains(kind) { throw SourceReadError.readFailed }
        let all = items[kind] ?? [:]
        return selectedIDs.flatMap { all[$0] ?? [] }.sorted { ($0.date ?? .distantPast) < ($1.date ?? .distantPast) }.prefix(limit).map { $0 }
    }
}
