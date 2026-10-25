import Foundation

// MARK: - DTOs (mirror PA contract)

struct CloudSourceRead: Codable, Identifiable, Hashable {
    let sourceId: String
    let sourceType: String
    let deviceId: String
    let displayName: String
    let syncEnabled: Bool
    let assistantEnabled: Bool
    let uploadFields: [String]
    let scopeKeys: [String]
    let lastSyncAt: Date?
    let lastSyncStatus: String?
    let lastErrorCode: String?
    let itemCount: Int

    var id: String { sourceId }
    var kind: SourceKind? { SourceKind(rawValue: sourceType) }

    enum CodingKeys: String, CodingKey {
        case sourceId = "source_id", sourceType = "source_type", deviceId = "device_id"
        case displayName = "display_name", syncEnabled = "sync_enabled"
        case assistantEnabled = "assistant_enabled", uploadFields = "upload_fields"
        case scopeKeys = "scope_keys", lastSyncAt = "last_sync_at"
        case lastSyncStatus = "last_sync_status", lastErrorCode = "last_error_code"
        case itemCount = "item_count"
    }
}

struct CloudContentRead: Codable, Identifiable, Hashable {
    let itemId: String
    let sourceId: String
    let sourceType: String
    let sourceDisplayName: String
    let kind: String
    let externalId: String
    let scopeKey: String
    let version: Int
    let title: String
    let body: String
    let startsAt: Date?
    let endsAt: Date?
    let dueAt: Date?
    let status: String?
    let location: String?
    let tags: [String]
    let sourceUpdatedAt: Date
    let syncedAt: Date

    var id: String { itemId }
    enum CodingKeys: String, CodingKey {
        case itemId = "item_id", sourceId = "source_id", sourceType = "source_type"
        case sourceDisplayName = "source_display_name", kind, externalId = "external_id"
        case scopeKey = "scope_key", version, title, body, startsAt = "starts_at"
        case endsAt = "ends_at", dueAt = "due_at", status, location, tags
        case sourceUpdatedAt = "source_updated_at", syncedAt = "synced_at"
    }
}

struct CloudContentList: Codable {
    let items: [CloudContentRead]
    let nextCursor: String?
    enum CodingKeys: String, CodingKey { case items, nextCursor = "next_cursor" }
}

struct CloudSyncItem: Encodable {
    let externalId: String
    let scopeKey: String
    let version: Int
    let updatedAt: Date
    let title: String?
    let body: String?
    let notes: String?
    let startsAt: Date?
    let endsAt: Date?
    let dueAt: Date?
    let location: String?
    let status: String?
    let tags: [String]?

    enum CodingKeys: String, CodingKey {
        case externalId = "external_id", scopeKey = "scope_key", version
        case updatedAt = "updated_at", title, body, notes
        case startsAt = "starts_at", endsAt = "ends_at", dueAt = "due_at"
        case location, status, tags
    }
}

struct CloudSyncResult: Codable {
    let batchId: String
    let created: Int
    let updated: Int
    let unchanged: Int
    let removed: Int
    let lastSyncAt: Date
    enum CodingKeys: String, CodingKey {
        case batchId = "batch_id", created, updated, unchanged, removed
        case lastSyncAt = "last_sync_at"
    }
}

// MARK: - Client

struct CloudSyncAPIError: LocalizedError {
    let status: Int
    let code: String
    let message: String
    var errorDescription: String? { message }
}

struct CloudSyncClient {
    var baseURL = PAConfig.baseURL
    var session: URLSession

    init(session: URLSession? = nil) {
        if let session { self.session = session; return }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 40
        configuration.waitsForConnectivity = false
        configuration.connectionProxyDictionary = [:]
        self.session = URLSession(configuration: configuration)
    }

    func listSources() async throws -> [CloudSourceRead] {
        try await request("/api/v1/data-sources", expect: [CloudSourceRead].self)
    }

    func createSource(type: String, displayName: String, scopeKeys: [String], fields: [String]) async throws -> CloudSourceRead {
        struct Body: Encodable {
            let source_type: String; let device_id: String; let display_name: String
            let scope_keys: [String]; let upload_fields: [String]
            let sync_enabled: Bool; let assistant_enabled: Bool
        }
        return try await request("/api/v1/data-sources", method: "POST",
                                 body: Body(source_type: type, device_id: CommuteConfig.installationID,
                                            display_name: displayName, scope_keys: scopeKeys,
                                            upload_fields: fields, sync_enabled: true,
                                            assistant_enabled: false),
                                 expect: CloudSourceRead.self)
    }

    func source(id: String) async throws -> CloudSourceRead {
        try await request("/api/v1/data-sources/\(id)", expect: CloudSourceRead.self)
    }

    struct SourcePatch: Encodable {
        var display_name: String?
        var sync_enabled: Bool?
        var assistant_enabled: Bool?
        var upload_fields: [String]?
        var scope_keys: [String]?
    }

    func updateSource(id: String, patch: SourcePatch) async throws -> CloudSourceRead {
        try await request("/api/v1/data-sources/\(id)", method: "PATCH", body: patch,
                          expect: CloudSourceRead.self)
    }

    func deleteSource(id: String) async throws {
        _ = try await raw("/api/v1/data-sources/\(id)", method: "DELETE")
    }

    func deleteItems(sourceId: String) async throws {
        _ = try await raw("/api/v1/data-sources/\(sourceId)/items", method: "DELETE")
    }

    func sync(sourceId: String, batchID: String, items: [CloudSyncItem], removed: [String] = []) async throws -> CloudSyncResult {
        struct Body: Encodable { let batch_id: String; let items: [CloudSyncItem]; let removed_external_ids: [String] }
        return try await request("/api/v1/data-sources/\(sourceId)/sync", method: "POST",
                                 body: Body(batch_id: batchID, items: items, removed_external_ids: removed),
                                 expect: CloudSyncResult.self)
    }

    func content(kind: String? = nil, query: String? = nil, limit: Int = 50, offset: Int = 0) async throws -> CloudContentList {
        var components = URLComponents()
        components.path = "/api/v1/content"
        var items = [URLQueryItem(name: "limit", value: String(limit)),
                     URLQueryItem(name: "offset", value: String(offset))]
        if let kind, !kind.isEmpty { items.append(URLQueryItem(name: "kind", value: kind)) }
        if let query, !query.isEmpty { items.append(URLQueryItem(name: "query", value: query)) }
        components.queryItems = items
        return try await request(components.string ?? "/api/v1/content", expect: CloudContentList.self)
    }

    func contentItem(id: String) async throws -> CloudContentRead {
        try await request("/api/v1/content/\(id)", expect: CloudContentRead.self)
    }

    private func request<T: Decodable>(_ path: String, method: String = "GET",
                                       body: Encodable? = nil, expect: T.Type) async throws -> T {
        let data = try await raw(path, method: method, body: body)
        do { return try CommuteJSON.decoder.decode(T.self, from: data) }
        catch { throw CloudSyncAPIError(status: 0, code: "decode_failed", message: "服务器返回格式不一致。") }
    }

    @discardableResult
    private func raw(_ path: String, method: String = "GET", body: Encodable? = nil) async throws -> Data {
        guard let token = TokenStore.token, !token.isEmpty else {
            throw CloudSyncAPIError(status: 401, code: "unauthenticated", message: "请先登录 PA 账号。")
        }
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw CloudSyncAPIError(status: 0, code: "bad_url", message: "接口地址无效。")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try CommuteJSON.encoder.encode(AnyCloudEncodable(body))
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw CloudSyncAPIError(status: 0, code: "bad_response", message: "服务器响应无效。")
        }
        guard (200..<300).contains(http.statusCode) else {
            struct Envelope: Decodable { struct E: Decodable { let code: String; let message: String }; let error: E }
            let envelope = try? CommuteJSON.decoder.decode(Envelope.self, from: data)
            throw CloudSyncAPIError(status: http.statusCode,
                                    code: envelope?.error.code ?? "http_\(http.statusCode)",
                                    message: envelope?.error.message ?? "请求失败（\(http.statusCode)）。")
        }
        return data
    }
}

private struct AnyCloudEncodable: Encodable {
    let wrapped: Encodable
    init(_ wrapped: Encodable) { self.wrapped = wrapped }
    func encode(to encoder: Encoder) throws { try wrapped.encode(to: encoder) }
}
