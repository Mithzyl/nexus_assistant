import Foundation

// MARK: - 来源类型与状态

enum SourceKind: String, CaseIterable, Identifiable {
    case calendar, reminders, notes

    var id: String { rawValue }

    var title: String {
        switch self {
        case .calendar: return "日历"
        case .reminders: return "提醒事项"
        case .notes: return "备忘录导入"
        }
    }

    var symbol: String {
        switch self {
        case .calendar: return "calendar"
        case .reminders: return "checklist"
        case .notes: return "note.text"
        }
    }

    var readScope: String {
        switch self {
        case .calendar: return "过去 7 天到未来 30 天的日程；只读，不修改原日程。"
        case .reminders: return "仅未完成的提醒事项；只读，不勾选或修改。"
        case .notes: return "由你主动选择或粘贴的笔记；不会扫描整个备忘录。"
        }
    }
}

enum SourcePermission: String {
    case notDetermined, denied, restricted, writeOnly, fullAccess
}

/// 来源的连接状态；失败不能被解释成“没有内容”。
enum SourceConnectionState: Equatable {
    case notConnected
    case permissionDenied
    case permissionRevoked
    case grantedNoSelection
    case selectedNotRead
    case selectedEmpty
    case ready(lastReadAt: Date, itemCount: Int)
    case failed(String)
    case paused(lastReadAt: Date?, itemCount: Int)

    var title: String {
        switch self {
        case .notConnected: return "未连接"
        case .permissionDenied: return "权限未允许"
        case .permissionRevoked: return "权限已被撤回"
        case .grantedNoSelection: return "已授权 · 尚未选择"
        case .selectedNotRead: return "已选择 · 尚未读取"
        case .selectedEmpty: return "已选择 · 范围内没有内容"
        case .ready(_, let count): return "已选择 · \(count) 项"
        case .failed: return "读取失败"
        case .paused(_, let count): return count > 0 ? "已暂停读取 · 保留 \(count) 项副本" : "已暂停读取"
        }
    }
}

// MARK: - 可选范围

struct SourceGroup: Identifiable, Hashable {
    let id: String
    let title: String
    let sources: [SelectableSource]
}

struct SelectableSource: Identifiable, Hashable {
    let id: String
    let title: String
    let subtitle: String
    let account: String
    let itemCount: Int
}

struct SourcePreviewItem: Identifiable, Hashable, Codable {
    let id: String
    let title: String
    let subtitle: String
    let date: Date?
    var scopeKey: String? = nil
    var endDate: Date? = nil
    var location: String? = nil
    var notes: String? = nil
    var status: String? = nil
}

// MARK: - 本地持久化

struct SourceSelection: Codable, Equatable {
    var selectedIDs: Set<String> = []
    var paused = false
    var cloudAssistantAllowed = false
    var lastReadAt: Date?
    var lastError: String?
    var previouslyAuthorized = false
}

struct SavedSourceSnapshot: Codable {
    var items: [SourcePreviewItem]
    var savedAt: Date
}

// MARK: - 备忘录导入

struct ImportedNote: Identifiable, Codable, Equatable {
    let id: String
    var title: String
    var body: String
    var source: String
    var matterTitle: String?
    var cloudAssistantAllowed: Bool
    var importedAt: Date
    var contentHash: String
}
