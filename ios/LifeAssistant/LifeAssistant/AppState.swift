import Foundation
import SwiftUI

struct AskSource: Identifiable, Hashable {
    let id = UUID()
    let title: String
    let route: Route
}

struct AskAnswer {
    let title: String
    let paragraphs: [String]
    let sources: [AskSource]
    let actions: [(String, Route)]
}

@MainActor
final class AppState: ObservableObject {
    /// Only explicit UI test launches may seed the in-memory demo. Normal runs
    /// start empty and never turn a failed backend request into sample content.
    let isDemo: Bool

    @Published var tab: Tab = .today
    @Published var path: [Route] = []
    @Published var showCapture = false
    @Published var capturePreset: String? = nil
    @Published var captureSayMode = false

    @Published var matters: [Matter] = AppState.initialMatters
    @Published var logs: [ActivityLog] = AppState.initialLogs
    @Published var memories: [MemoryItem] = SampleData.memories

    @Published var processedSources: Set<String> = ["movie", "wish", "coffee-order"]
    @Published var pendingAmbiguity = true
    @Published var ambiguityResolved: String? = nil
    @Published var receiptUndone = false

    @Published var checks: Set<String> = []
    @Published var transportConfirmed = false
    @Published var packingStarted = false
    @Published var wishScheduled = false
    @Published var travelAccepted = false
    @Published var contacts: Set<String> = []

    @Published var preferences = Preferences()
    @Published var initialQuestion: String? = nil
    /// 验证/通知深链用：登录完成后打开指定 advisory（只读），由 `START_ADVISORY_ID` 注入。
    @Published var pendingAdvisoryID: String? = nil

    /// Launch hooks used by the screenshot pass: `START_TAB=ask`, `START_ROUTE=refund`, `START_CAPTURE=1`.
    /// `demoMode` 仅供单元测试/预览显式注入演示数据；正式运行与真实 HTTP UI 验收
    /// 仍只由 `PA_DEMO=1` 控制，不会因为测试需要而默认开启。
    init(demoMode: Bool? = nil) {
        let env = ProcessInfo.processInfo.environment
        isDemo = demoMode ?? (env["PA_DEMO"] == "1")
        if !isDemo {
            matters = []
            logs = []
            memories = []
            processedSources = []
            pendingAmbiguity = false
        }
        if let requested = env["START_TAB"].flatMap(Tab.init(rawValue:)) {
            tab = requested
        }
        if let requested = env["START_ROUTE"].flatMap(Self.route(named:)) {
            path = [requested]
        }
        if env["START_CAPTURE"] == "1" {
            captureSayMode = env["START_SAY"] == "1"
            showCapture = true
        }
        if let question = env["START_ASK"], !question.isEmpty {
            initialQuestion = question
        }
        if let advisoryId = env["START_ADVISORY_ID"], !advisoryId.isEmpty {
            pendingAdvisoryID = advisoryId
        }
        if env["START_PENDING"] == "0" {
            pendingAmbiguity = false
        }
        if env["START_LOGS"] == "empty" {
            logs = []
        }
        if env["START_LONG"] == "1" {
            logs.insert(
                ActivityLog(
                    id: "long",
                    title: "周末短途旅行的交通与住宿确认都已整理进同一件事务",
                    detail: "订单、改期通知和待联系事项都归在这件事下，之后可以接着问",
                    target: .project
                ),
                at: 0
            )
        }
        if let resolved = env["START_AMBIGUITY"], !resolved.isEmpty {
            resolveAmbiguity(resolved)
        }
    }

    static func route(named name: String) -> Route? {
        switch name {
        case "refund": return .refund
        case "shelf": return .shelf
        case "ambiguity": return .ambiguity
        case "prepare": return .prepare
        case "ticket": return .ticket
        case "wish": return .wish
        case "wishPlan": return .wishPlan
        case "project": return .project
        case "travel": return .travel
        case "memories": return .memories
        case "memory": return .memory("cinema")
        case "lesson": return .lesson
        case "privacy": return .privacy
        case "setup": return .setup
        case "info": return .info
        case "source": return .source("order")
        case "added": return .added("demo-lamp")
        case "commute", "commuteSettings": return .commuteSettings
        case "commuteAdvisory": return .commuteAdvisory("demo-advisory-3")
        case "commuteNotifications": return .commuteNotifications
        case "dataSources": return .dataSources
        default: return nil
        }
    }

    // MARK: - Navigation

    func select(_ next: Tab) {
        tab = next
    }

    func open(_ route: Route) {
        path.append(route)
    }

    func openCapture(preset: String? = nil, say: Bool = false) {
        capturePreset = preset
        captureSayMode = say
        showCapture = true
    }

    // MARK: - Reads

    func matter(_ id: String) -> Matter? {
        matters.first { $0.id == id }
    }

    var coffee: Matter? { matter("coffee") }
    var shelf: Matter? { matter("shelf") }

    var coffeeStage: RefundStage {
        coffee?.refundStage ?? .requested
    }

    var shelfStage: RefundStage {
        shelf?.refundStage ?? .requested
    }

    /// `shelfInProgress = shelfInitiated || ambiguityResolved == "shelf"`（V3）。
    /// 本页没有第二套到账回执模拟，`shelfInitiated` 恒为 false。
    var shelfInProgress: Bool {
        ambiguityResolved == "shelf"
    }

    var cinemaMemory: MemoryItem? {
        memories.first { $0.id == "cinema" }
    }

    var activeChecklist: [(String, String, String)] {
        var items: [(String, String, String)] = []
        if cinemaMemory?.enabled == true {
            items.append(("coat", "薄外套", "影院空调冷 · 来自你的经验"))
        }
        items.append(("ticket", "电影票", "7排8座 · 取票码 DEMO 4826 0318"))
        items.append(("seat", "座位确认", "3号厅 · 19:30 开场"))
        return items
    }

    var projectProgress: Int {
        1 + (transportConfirmed ? 1 : 0) + (packingStarted ? 1 : 0)
    }

    // MARK: - Refund

    private func updateCoffeeStage(_ stage: RefundStage) {
        guard let index = matters.firstIndex(where: { $0.id == "coffee" }) else { return }
        matters[index].refundStage = stage
        switch stage {
        case .requested:
            matters[index].subtitle = "晨间生活店 · 已申请退款"
            matters[index].nextStep = "等待商家处理"
        case .merchantInitiated:
            matters[index].subtitle = "晨间生活店 · 商家已发起，未到账"
            matters[index].nextStep = "等到账后再确认"
        case .received:
            matters[index].subtitle = "晨间生活店 · 已退回原账户"
            matters[index].nextStep = "无需操作"
        }
    }

    func undoReceipt() {
        guard coffeeStage == .received else { return }
        updateCoffeeStage(.merchantInitiated)
        receiptUndone = true
        logs.insert(
            ActivityLog(id: "undo-receipt", title: "撤销了这次到账更新", detail: "回执仍然保留，但没有再算作已到账", target: .refund),
            at: 0
        )
    }

    func reapplyReceipt() {
        guard receiptUndone else { return }
        updateCoffeeStage(.received)
        receiptUndone = false
        logs.insert(
            ActivityLog(id: "reapply-receipt", title: "重新用了这份回执", detail: "你明确点了重新应用，状态才更新", target: .refund),
            at: 0
        )
    }

    // MARK: - Ambiguity

    func resolveAmbiguity(_ choice: String) {
        ambiguityResolved = choice
        pendingAmbiguity = false
        let name = choice == "coffee" ? "便携咖啡机" : "收纳架"
        let target: Route = choice == "shelf" ? .shelf : .refund
        logs.insert(
            ActivityLog(id: "ambiguity", title: "把 ¥129 的通知归到了\(name)", detail: "只关联了通知，没有标记到账", target: target),
            at: 0
        )
        // 改关联时，旧的那件事不再带着这条通知（V3 验收 4）。
        let marker = " · 已关联新通知"
        for id in ["coffee", "shelf"] {
            guard let index = matters.firstIndex(where: { $0.id == id }) else { continue }
            if matters[index].subtitle.hasSuffix(marker) {
                matters[index].subtitle.removeLast(marker.count)
            }
        }
        if let index = matters.firstIndex(where: { $0.id == choice }) {
            matters[index].subtitle += marker
        }
    }

    // MARK: - Capture

    func ingest(source: DemoSource) -> CaptureOutcome {
        if !isDemo {
            return CaptureOutcome(
                title: "尚未接入内容整理",
                body: "当前版本没有连接真实的整理服务，因此没有保存任何虚构结果。",
                route: .info,
                duplicate: false
            )
        }
        if source.id == "ambiguous" && ambiguityResolved == nil {
            pendingAmbiguity = true
            return CaptureOutcome(
                title: "还差你确认一小步",
                body: "¥129 对应两件正在退款的商品。通知没有订单号，先不关联，也不会标记到账。",
                route: .ambiguity,
                duplicate: false
            )
        }

        if processedSources.contains(source.id) {
            if source.id == "receipt" && receiptUndone {
                return CaptureOutcome(
                    title: "你撤销过这次更新，保持原样",
                    body: "同一份回执已经处理过，而且你选择了撤销。不会因为重复交入而悄悄重新应用。",
                    route: .refund,
                    duplicate: true
                )
            }
            let target: Route
            switch source.id {
            case "movie": target = .prepare
            case "wish": target = .wish
            case "neworder": target = .added("demo-lamp")
            case "ambiguous": target = .refund
            default: target = .refund
            }
            return CaptureOutcome(
                title: "已经有了，没有重复收下",
                body: "识别到相同的订单、票据或原始内容，继续使用原来的事务。",
                route: target,
                duplicate: true
            )
        }

        processedSources.insert(source.id)

        switch source.id {
        case "notice":
            let wasReceived = coffeeStage == .received
            if !wasReceived {
                updateCoffeeStage(.merchantInitiated)
            }
            logs.insert(
                ActivityLog(
                    id: "notice",
                    title: wasReceived ? "补充了一条较早的进度通知" : "咖啡机退款有了新进展",
                    detail: wasReceived ? "较早的通知不覆盖已到账结果" : "订单号一致 · 商家已发起，不等于已到账",
                    target: .refund
                ),
                at: 0
            )
            return CaptureOutcome(
                title: wasReceived ? "补充了依据，保留已到账状态" : "已更新原来的退款事务",
                body: wasReceived
                    ? "同一订单的较早进度通知已归入原事务，它不会覆盖后来已有的到账回执。"
                    : "订单号和金额都一致。商家已发起，但还没有到账，所以状态停在“商家已发起”。",
                route: .refund,
                duplicate: false
            )

        case "receipt":
            updateCoffeeStage(.received)
            receiptUndone = false
            logs.insert(
                ActivityLog(id: "receipt", title: "这次到账，记下了", detail: "订单号一致 · 依据是回执，不只看金额", target: .refund),
                at: 0
            )
            return CaptureOutcome(
                title: "这次到账，记下了",
                body: "订单号与金额都一致。回执作为到账依据保存，可随时撤销这次更新。",
                route: .refund,
                duplicate: false
            )

        case "neworder":
            if matter("demo-lamp") == nil {
                matters.insert(
                    Matter(
                        id: "demo-lamp",
                        kind: .order,
                        title: "暖光台灯",
                        subtitle: "晚间生活馆 · 等待发货",
                        status: .active,
                        amount: "¥89.00",
                        dateText: "10月23日",
                        refundStage: nil,
                        detail: [
                            DetailRow(label: "商家", value: "晚间生活馆"),
                            DetailRow(label: "订单号", value: "DEMO-LAMP-1023"),
                            DetailRow(label: "金额", value: "¥89.00"),
                            DetailRow(label: "状态", value: "已付款，等待发货")
                        ],
                        evidenceId: "lamp",
                        nextStep: "等待发货",
                        route: .added("demo-lamp")
                    ),
                    at: 0
                )
            }
            logs.insert(
                ActivityLog(id: "neworder", title: "台灯订单收好了", detail: "原文和订单号都保留 · 后续消息归到同一件事", target: .added("demo-lamp")),
                at: 0
            )
            return CaptureOutcome(
                title: "台灯订单收好了",
                body: "原文和订单号都保留。以后有物流或售后消息，会归到同一件事。",
                route: .added("demo-lamp"),
                duplicate: false
            )

        default:
            logs.insert(
                ActivityLog(id: "movie", title: "电影票已归好", detail: "关联了影院经验 · 明晚出门前用得上", target: .prepare),
                at: 0
            )
            return CaptureOutcome(
                title: "已经收好了",
                body: "这类材料会继续沿用原来的事务。",
                route: .prepare,
                duplicate: true
            )
        }
    }

    func ingest(text raw: String) -> CaptureOutcome {
        if !isDemo {
            return CaptureOutcome(
                title: "尚未接入内容整理",
                body: "当前版本没有连接真实的整理服务，因此没有保存任何虚构结果。",
                route: .info,
                duplicate: false
            )
        }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)

        if text.contains("柳江") || text.contains("散步") {
            return CaptureOutcome(
                title: "这个心愿，已经在这里了",
                body: "认出了“柳江边散步”。保留已有心愿，不重复创建；你可以接着为它找时间。",
                route: .wish,
                duplicate: true
            )
        }

        if text.contains("下次") || text.contains("记得") || text.contains("外套") || text.contains("经验") {
            let item = MemoryItem(
                id: "spoken-\(UUID().uuidString)",
                text: text,
                place: "随手记下",
                date: SampleData.today,
                source: "你主动写下：“\(text)”",
                enabled: true
            )
            memories.insert(item, at: 0)
            logs.insert(
                ActivityLog(id: item.id, title: "记住了你的一点经验", detail: "来自你刚才主动交给我的一句话", target: .memory(item.id)),
                at: 0
            )
            return CaptureOutcome(
                title: "这句经验，替你收好了",
                body: "放进了个人记忆。它不是凭空生成的结论，你可以随时修改或停用。",
                route: .memory(item.id),
                duplicate: false
            )
        }

        let id = "context-\(UUID().uuidString)"
        let title = String(text.prefix(18))
        matters.insert(
            Matter(
                id: id,
                kind: .unknown,
                title: title,
                subtitle: "待整理 · 未擅自安排",
                status: .active,
                amount: nil,
                dateText: SampleData.today,
                refundStage: nil,
                detail: [DetailRow(label: "原文", value: text)],
                evidenceId: nil,
                nextStep: "确认它属于哪件事",
                route: .added(id)
            ),
            at: 0
        )
        logs.insert(
            ActivityLog(id: id, title: "先替你留住这句话", detail: "没有足够依据判断类别，未擅自安排", target: .added(id)),
            at: 0
        )
        return CaptureOutcome(
            title: "先收下，不替你猜",
            body: "这句话已保留在事务里。当前示例无法可靠判断更多信息，所以没有自动设置日期、金额或待办。",
            route: .added(id),
            duplicate: false
        )
    }

    // MARK: - Memories

    func toggleMemory(_ id: String) {
        guard let index = memories.firstIndex(where: { $0.id == id }) else { return }
        memories[index].enabled.toggle()
    }

    func updateMemory(_ id: String, text: String) {
        guard let index = memories.firstIndex(where: { $0.id == id }) else { return }
        memories[index].text = text
    }

    func addLesson(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let item = MemoryItem(
            id: "lesson-\(UUID().uuidString)",
            text: trimmed,
            place: "主动记下",
            date: SampleData.today,
            source: "你在“记住一点经验”里主动写下：“\(trimmed)”",
            enabled: true
        )
        memories.insert(item, at: 0)
    }

    // MARK: - Other actions

    func toggleCheck(_ id: String) {
        if checks.contains(id) {
            checks.remove(id)
        } else {
            checks.insert(id)
        }
    }

    func confirmTransport() {
        transportConfirmed = true
    }

    func startPacking() {
        packingStarted = true
        transportConfirmed = true
    }

    func confirmWishSchedule() {
        wishScheduled = true
        logs.insert(
            ActivityLog(id: "wish-plan", title: "把散步放进了安排", detail: "明天 15:00—17:00 · 60 分钟", target: .wish),
            at: 0
        )
    }

    func acceptTravelChange() {
        travelAccepted = true
        contacts = ["接站师傅", "活动联系人"]
        logs.insert(
            ActivityLog(id: "travel", title: "改期影响已确认", detail: "只生成了待联系事项，没有代发消息", target: .travel),
            at: 0
        )
    }

    func toggleContact(_ id: String) {
        if contacts.contains(id) {
            contacts.remove(id)
        } else {
            contacts.insert(id)
        }
    }

    // MARK: - Ask

    func answer(for question: String) -> AskAnswer {
        let q = question

        if q.contains("退款") || q.contains("咖啡机") || q.contains("到账") {
            var text: [String]
            switch coffeeStage {
            case .requested:
                text = ["10月17日申请退款 ¥129.00，目前只有申请记录，还没有商家处理的消息。"]
            case .merchantInitiated:
                text = [
                    "商家已在 10月21日发起退款，预计 1—3 个工作日退回原支付账户。",
                    "这条通知不是到账凭证，所以我还没有把它标成已到账。"
                ]
            case .received:
                text = ["10月23日的到账回执显示，¥129.00 已退回原支付账户。"]
            }
            if receiptUndone {
                text.append("你撤销过这次到账更新，所以现在按“商家已发起”显示；回执还在，想恢复需要你点一次重新应用。")
            }
            text.append("依据是同一订单号 DEMO-20261015-129，不是只看金额。")
            return AskAnswer(
                title: "咖啡机退款，\(coffeeStage == .received ? "已经到账。" : "还差到账这一步。")",
                paragraphs: text,
                sources: [AskSource(title: "咖啡机订单与退款记录", route: .source("order"))],
                actions: [("打开退款事务", .refund)]
            )
        }

        if q.contains("电影") || q.contains("准备") || q.contains("带什么") {
            var items = ["电影票 7排8座，取票码 DEMO 4826 0318。", "3号厅，19:30 开场，建议 19:00 前到。"]
            if cinemaMemory?.enabled == true {
                items.insert("带一件薄外套——你记过这家影院空调冷。", at: 0)
            } else {
                items.append("你停用了影院经验，所以清单里不再出现薄外套。")
            }
            return AskAnswer(
                title: "明晚看电影，带这些就够。",
                paragraphs: items,
                sources: [
                    AskSource(title: "电影电子票", route: .source("ticket")),
                    AskSource(title: "影院经验", route: .memory("cinema"))
                ],
                actions: [("打开出门清单", .prepare)]
            )
        }

        if q.contains("散步") || q.contains("心愿") || q.contains("柳江") {
            let tail = wishScheduled ? "已经加入安排。" : "还没有加入安排，确认后才会写进日程。"
            return AskAnswer(
                title: "柳江边散步，安排在明天下午。",
                paragraphs: ["明天 15:00—17:00 有 2 小时空档，这个心愿需要 60 分钟。", tail],
                sources: [AskSource(title: "保存的心愿与空闲时间", route: .source("wish"))],
                actions: [("打开心愿", .wish)]
            )
        }

        if q.contains("旅行") || q.contains("准备到") || q.contains("交通") {
            let progress = projectProgress
            let next = progress >= 3 ? "行李也整理好了，可以出发。" : (transportConfirmed ? "交通已确认，接下来整理行李。" : "还差确认交通，再整理行李。")
            return AskAnswer(
                title: "旅行准备，\(progress)/3。",
                paragraphs: ["已决定住江景客栈，但没有代订。", "改期通知把到达时间改到 14:00，接站和活动两处要重新确认。", next],
                sources: [
                    AskSource(title: "旅行准备笔记", route: .source("project")),
                    AskSource(title: "原行程与改期通知", route: .source("travel"))
                ],
                actions: [("打开旅行准备", .project)]
            )
        }

        return AskAnswer(
            title: "这件事，还没有找到依据。",
            paragraphs: [
                "当前保存的材料里没有能和它对应的订单、票据或记录。",
                "你可以把相关内容交给助手，我再按原文和编号去核对，不靠猜。"
            ],
            sources: [],
            actions: [("交给助手", .captureSource("notice"))]
        )
    }

    // MARK: - Seed data

    static let initialLogs: [ActivityLog] = [
        ActivityLog(id: "movie", title: "电影票已归好", detail: "关联了影院经验 · 明晚出门前用得上", target: .prepare),
        ActivityLog(id: "coffee-order", title: "咖啡机订单已收好", detail: "退款进度仍在等后续消息", target: .refund),
        ActivityLog(id: "wish", title: "把散步的念头留住了", detail: "柳江边 · 需要 60 分钟", target: .wish)
    ]

    static let initialMatters: [Matter] = [
        Matter(
            id: "coffee",
            kind: .refund,
            title: "便携咖啡机",
            subtitle: "晨间生活店 · 已申请退款",
            status: .active,
            amount: "¥129.00",
            dateText: "10月17日",
            refundStage: .requested,
            detail: [
                DetailRow(label: "商家", value: "晨间生活店"),
                DetailRow(label: "订单号", value: "DEMO-20261015-129"),
                DetailRow(label: "金额", value: "¥129.00"),
                DetailRow(label: "申请时间", value: "10月17日")
            ],
            evidenceId: "order",
            nextStep: "等待商家处理",
            route: .refund
        ),
        Matter(
            id: "shelf",
            kind: .refund,
            title: "收纳架",
            subtitle: "家居小店 · 已申请退款",
            status: .active,
            amount: "¥129.00",
            dateText: "10月20日",
            refundStage: .requested,
            detail: [
                DetailRow(label: "商家", value: "家居小店"),
                DetailRow(label: "订单号", value: "DEMO-SHELF-1020"),
                DetailRow(label: "金额", value: "¥129.00"),
                DetailRow(label: "申请时间", value: "10月20日")
            ],
            evidenceId: "ambiguous",
            nextStep: "等待商家处理",
            route: .shelf
        ),
        Matter(
            id: "movie",
            kind: .ticket,
            title: "流浪地球 3",
            subtitle: "明晚 19:30 · 万达影城柳南店",
            status: .active,
            amount: nil,
            dateText: "10月24日",
            refundStage: nil,
            detail: [
                DetailRow(label: "影厅", value: "3号厅"),
                DetailRow(label: "座位", value: "7排8座"),
                DetailRow(label: "取票码", value: "DEMO 4826 0318")
            ],
            evidenceId: "ticket",
            nextStep: "出门前检查清单",
            route: .ticket
        ),
        Matter(
            id: "wish",
            kind: .wish,
            title: "去柳江边散散步",
            subtitle: "需要 60 分钟 · 还没安排",
            status: .active,
            amount: nil,
            dateText: "10月20日",
            refundStage: nil,
            detail: [
                DetailRow(label: "需要", value: "60 分钟"),
                DetailRow(label: "可用", value: "明天 15:00—17:00")
            ],
            evidenceId: "wish",
            nextStep: "挑一个空档",
            route: .wish
        ),
        Matter(
            id: "project",
            kind: .project,
            title: "周末短途旅行准备",
            subtitle: "3 步里完成 1 步",
            status: .active,
            amount: nil,
            dateText: "10月18日",
            refundStage: nil,
            detail: [
                DetailRow(label: "已决定", value: "住江景客栈，未代订"),
                DetailRow(label: "未决定", value: "交通与出发时间")
            ],
            evidenceId: "project",
            nextStep: "确认交通",
            route: .project
        ),
        Matter(
            id: "travel",
            kind: .travel,
            title: "行程改期影响",
            subtitle: "到达改到 14:00 · 2 处要确认",
            status: .active,
            amount: nil,
            dateText: "10月26日",
            refundStage: nil,
            detail: [
                DetailRow(label: "原到达", value: "11:00"),
                DetailRow(label: "新到达", value: "14:00"),
                DetailRow(label: "受影响", value: "接站 11:30 · 活动 13:00")
            ],
            evidenceId: "travel",
            nextStep: "确认接站与活动",
            route: .travel
        )
    ]
}
