import Foundation

// MARK: - 服务端 DTO（与 /api/v1 OpenAPI 对齐）

enum AskStatus: String, Codable, CaseIterable {
    case pending, running, succeeded, failed, cancelled, unknown

    var isTerminal: Bool {
        switch self {
        case .succeeded, .failed, .cancelled, .unknown: return true
        case .pending, .running: return false
        }
    }
}

struct AskCreateResponse: Codable {
    let localRunId: String
    let nexusRunId: String?
    let workflowExecutionId: String?
    let status: AskStatus
    let replayed: Bool

    enum CodingKeys: String, CodingKey {
        case localRunId = "local_run_id"
        case nexusRunId = "nexus_run_id"
        case workflowExecutionId = "workflow_execution_id"
        case status, replayed
    }
}

struct AskRead: Codable, Identifiable {
    let localRunId: String
    let nexusRunId: String?
    let workflowExecutionId: String?
    let status: AskStatus
    let replayed: Bool
    let requestText: String
    let lastEventSequence: Int
    let errorCode: String?
    let answer: String?
    let completedAt: Date?
    let createdAt: Date

    var id: String { localRunId }

    enum CodingKeys: String, CodingKey {
        case localRunId = "local_run_id"
        case nexusRunId = "nexus_run_id"
        case workflowExecutionId = "workflow_execution_id"
        case status, replayed
        case requestText = "request_text"
        case lastEventSequence = "last_event_sequence"
        case errorCode = "error_code"
        case answer
        case completedAt = "completed_at"
        case createdAt = "created_at"
    }
}

enum AskChatClassification: String, Codable, Equatable {
    case chat
    case notChat = "not_chat"
    case unknown
}

struct AskVerifyItem: Codable, Equatable {
    let localRunId: String
    let classification: AskChatClassification

    enum CodingKeys: String, CodingKey {
        case localRunId = "local_run_id"
        case classification
    }
}

struct AskVerifyResponse: Codable, Equatable {
    let results: [AskVerifyItem]
}

struct PALoginResponse: Codable {
    let token: String
    let expiresAt: Date
    let userId: String

    enum CodingKeys: String, CodingKey {
        case token
        case expiresAt = "expires_at"
        case userId = "user_id"
    }
}

/// 实际续期响应（`POST /api/v1/auth/session/renew`）：
/// `{user, expires_at, server_time, token_rotated}`，不返回新令牌。
/// 服务器只会把同一会话延长到 server_time + TTL，不会换号、不会复活已撤销会话。
struct PARenewalResponse: Decodable {
    let user: PAMe
    let expiresAt: Date
    let serverTime: Date?
    let tokenRotated: Bool

    enum CodingKeys: String, CodingKey {
        case user
        case expiresAt = "expires_at"
        case serverTime = "server_time"
        case tokenRotated = "token_rotated"
    }
}

struct PAMe: Codable, Equatable {
    let userId: String
    let email: String
    let displayName: String

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case email
        case displayName = "display_name"
    }
}

struct PAErrorEnvelope: Codable {
    struct Body: Codable {
        let code: String
        let message: String
        let retryable: Bool
    }
    let error: Body
    let requestId: String?

    enum CodingKeys: String, CodingKey {
        case error
        case requestId = "request_id"
    }
}

// MARK: - 错误

struct PAError: Error, Equatable {
    enum Kind: Equatable {
        case unauthenticated
        case forbidden
        case notFound
        case conflict
        case validation
        case rateLimited
        case offline
        case timeout
        case server
        case cancelled
        case uncertain
    }

    let kind: Kind
    let status: Int?
    let code: String
    let message: String
    let retryable: Bool
    let requestId: String?
    let retryAfter: TimeInterval?

    var userMessage: String {
        switch kind {
        case .unauthenticated: return "登录状态已失效，请重新登录。"
        case .forbidden: return "没有权限查看这条问答。"
        case .notFound: return "这条问答不存在或已不可用。"
        case .conflict: return "这条请求当前不可取消。"
        case .validation: return "提问内容不符合要求。"
        case .rateLimited: return "请求太频繁，请稍后再试。"
        case .offline: return "网络不可用，稍后可以重试。"
        case .timeout: return "请求超时了。"
        case .server: return "服务暂时不可用，稍后再试。"
        case .cancelled: return "已取消。"
        case .uncertain: return "提交结果暂不确定。"
        }
    }
}

// MARK: - 本地记录

enum AskPhase: String, Codable {
    case submitting      // 请求已发出，尚未收到 202
    case submissionUncertain // 超时/504：服务器可能已创建，保留幂等键
    case queued          // 已拿到 ask_id，尚未开始查询
    case running
    case succeeded
    case failed
    case cancelled
    case unknown
}

struct AskItem: Identifiable, Codable, Equatable {
    let id: String              // 本地记录 ID，不暴露给服务器
    var question: String
    var idempotencyKey: String
    var serverId: String?
    var status: AskStatus?
    var phase: AskPhase
    var answer: String?
    var completedAt: Date?
    var createdAt: Date
    var updatedAt: Date
    var errorMessage: String?
    var pollingPaused: Bool
    /// 服务器核对结果；nil 表示尚未核对（旧缓存或核对失败）。
    var chatClassification: AskChatClassification?
    var verifiedAt: Date?

    init(question: String, idempotencyKey: String = UUID().uuidString) {
        self.id = UUID().uuidString
        self.question = question
        self.idempotencyKey = idempotencyKey
        self.phase = .submitting
        self.createdAt = Date()
        self.updatedAt = Date()
        self.pollingPaused = false
    }

    var isActive: Bool {
        switch phase {
        case .submitting, .submissionUncertain, .queued, .running, .unknown: return true
        case .succeeded, .failed, .cancelled: return false
        }
    }

    /// 服务器终态中不可回溯的状态：已确认取消/已完成，不被迟到响应覆盖。
    var isLocked: Bool {
        phase == .cancelled || (phase == .succeeded && (answer?.isEmpty == false))
    }
}
