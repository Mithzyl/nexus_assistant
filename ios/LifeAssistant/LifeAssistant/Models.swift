import Foundation

enum Tab: String, CaseIterable, Identifiable {
    case today, matters, ask, me

    var id: String { rawValue }

    var title: String {
        switch self {
        case .today: return "今天"
        case .matters: return "事务"
        case .ask: return "助手"
        case .me: return "我的"
        }
    }

    var icon: String {
        switch self {
        case .today: return "sun.max"
        case .matters: return "square.stack.3d.up"
        case .ask: return "bubble.left.and.text.bubble.right"
        case .me: return "person.crop.circle"
        }
    }
}

enum Route: Hashable {
    case captureSource(String)
    case source(String)
    case refund
    case shelf
    case ambiguity
    case prepare
    case ticket
    case wish
    case wishPlan
    case project
    case travel
    case added(String)
    case memories
    case memory(String)
    case lesson
    case privacy
    case setup
    case info
    case commuteSettings
    case commuteAdvisory(String)
    case commuteNotifications
    case dataSources
    case sourceDetail(String)
    case noteImport
}

enum MatterKind: String {
    case refund, order, ticket, wish, project, travel, unknown

    var label: String {
        switch self {
        case .refund: return "退款"
        case .order: return "订单"
        case .ticket: return "票据"
        case .wish: return "心愿"
        case .project: return "项目"
        case .travel: return "行程"
        case .unknown: return "待整理"
        }
    }

    var icon: String {
        switch self {
        case .refund: return "arrow.uturn.backward"
        case .order: return "shippingbox"
        case .ticket: return "ticket"
        case .wish: return "sparkles"
        case .project: return "checklist"
        case .travel: return "airplane"
        case .unknown: return "tray"
        }
    }
}

enum MatterStatus: String {
    case active, done

    var label: String {
        switch self {
        case .active: return "进行中"
        case .done: return "已完成"
        }
    }
}

enum RefundStage: String, CaseIterable, Comparable {
    case requested
    case merchantInitiated
    case received

    var label: String {
        switch self {
        case .requested: return "已申请"
        case .merchantInitiated: return "商家已发起"
        case .received: return "已到账"
        }
    }

    var detail: String {
        switch self {
        case .requested: return "已提交申请，等待商家处理"
        case .merchantInitiated: return "商家已发起退款，尚未到账"
        case .received: return "款项已退回原支付账户"
        }
    }

    var index: Int {
        switch self {
        case .requested: return 0
        case .merchantInitiated: return 1
        case .received: return 2
        }
    }

    static func < (lhs: RefundStage, rhs: RefundStage) -> Bool {
        lhs.index < rhs.index
    }
}

struct DetailRow: Identifiable, Hashable {
    let id = UUID()
    let label: String
    let value: String
}

struct Evidence: Identifiable, Hashable {
    let id: String
    let title: String
    let meta: String
    let text: String
}

struct Matter: Identifiable, Hashable {
    let id: String
    var kind: MatterKind
    var title: String
    var subtitle: String
    var status: MatterStatus
    var amount: String?
    var dateText: String
    var refundStage: RefundStage?
    var detail: [DetailRow]
    var evidenceId: String?
    var nextStep: String?
    var route: Route
}

struct MemoryItem: Identifiable, Hashable {
    let id: String
    var text: String
    var place: String
    var date: String
    var source: String
    var enabled: Bool
}

struct ActivityLog: Identifiable, Hashable {
    let id: String
    let title: String
    let detail: String
    let target: Route?
}

struct DemoSource: Identifiable, Hashable {
    let id: String
    let title: String
    let hint: String
    let channel: String
    let text: String
}

struct CaptureOutcome: Identifiable {
    let id = UUID()
    let title: String
    let body: String
    let route: Route?
    let duplicate: Bool
}

struct Preferences {
    var keepOriginalText = true
    var retainDays = 30
    var notifyBeforeGoingOut = true
}

enum SampleData {
    static var today: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "M月d日 EEEE"
        return formatter.string(from: Date())
    }

    static let evidence: [String: Evidence] = [
        "order": Evidence(
            id: "order",
            title: "咖啡机订单与退款记录",
            meta: "手动保存 · 10月17日",
            text: """
            示例订单
            商品：便携咖啡机
            实付：¥129.00
            商家：晨间生活店
            订单号：DEMO-20261015-129
            10月15日：下单付款
            10月17日：申请退款 ¥129.00
            当前记录：等待到账；尚未保存明确到账凭据。
            """
        ),
        "receipt": Evidence(
            id: "receipt",
            title: "退款到账回执",
            meta: "手动保存 · 10月23日",
            text: """
            退款到账回执（示例）
            订单号：DEMO-20261015-129
            商品：便携咖啡机
            退款金额：¥129.00
            状态：已退回原支付账户
            到账日期：2026年10月23日
            """
        ),
        "notice": Evidence(
            id: "notice",
            title: "退款进度通知",
            meta: "手动保存 · 10月21日",
            text: """
            晨间生活店：订单 DEMO-20261015-129，便携咖啡机退款 ¥129.00
            已由商家发起，预计 1—3 个工作日退回原支付账户。
            此通知不是到账凭证。
            """
        ),
        "ambiguous": Evidence(
            id: "ambiguous",
            title: "一条不完整的退款通知",
            meta: "手动保存 · 10月22日",
            text: """
            您有一笔 ¥129.00 退款正在处理。
            本条通知未包含商家名称、订单号或到账凭据。
            """
        ),
        "ticket": Evidence(
            id: "ticket",
            title: "电影电子票",
            meta: "手动保存 · 10月21日",
            text: """
            示例电影票
            影片：流浪地球 3
            时间：2026年10月24日 19:30
            影院：万达影城（柳南店）
            影厅：3号厅
            座位：7排8座
            取票码：DEMO 4826 0318
            仅供原型体验，不可用于真实取票。
            """
        ),
        "wish": Evidence(
            id: "wish",
            title: "保存的心愿与空闲时间",
            meta: "个人记录 · 10月20日",
            text: """
            心愿：去柳江边散散步，留出60分钟。
            可用时间：2026年10月24日 15:00—17:00。
            这些是手动录入的示例信息；没有连接日历、天气或地图。
            """
        ),
        "project": Evidence(
            id: "project",
            title: "旅行准备笔记",
            meta: "个人记录 · 10月18日",
            text: """
            周末短途旅行准备
            已决定：住江景客栈，酒店已选好但未代订。
            尚未决定：坐什么交通工具、几点出发。
            接下来：确认交通，再整理行李。
            """
        ),
        "travel": Evidence(
            id: "travel",
            title: "原行程与改期通知",
            meta: "示例通知 · 手动导入",
            text: """
            原行程｜10月26日
            预计到达 11:00
            接站安排 11:30
            活动开始 13:00

            改期通知（示例）
            预计到达时间改为 14:00。
            请自行确认后续接站及活动安排。
            此演示不会发送消息或办理改签。
            """
        ),
        "lamp": Evidence(
            id: "lamp",
            title: "暖光台灯订单",
            meta: "分享的页面 · 10月23日",
            text: """
            暖光台灯 · ¥89.00
            商家：晚间生活馆
            订单号：DEMO-LAMP-1023
            已付款，等待发货
            下单日期：2026年10月23日
            """
        )
    ]

    static let demoSources: [DemoSource] = [
        DemoSource(
            id: "notice",
            title: "退款进度通知",
            hint: "更新已有退款，不当作已到账",
            channel: "通知内容",
            text: "晨间生活店：订单 DEMO-20261015-129，便携咖啡机退款 ¥129.00 已由商家发起，预计1—3个工作日退回原支付账户。此通知不是到账凭证。"
        ),
        DemoSource(
            id: "receipt",
            title: "退款到账回执",
            hint: "金额、订单号一致，可直接关联",
            channel: "选中的回执",
            text: "退款到账回执（示例）\n订单号：DEMO-20261015-129\n商品：便携咖啡机\n退款金额：¥129.00\n状态：已退回原支付账户\n到账日期：2026年10月23日"
        ),
        DemoSource(
            id: "ambiguous",
            title: "一条不完整的退款通知",
            hint: "只有¥129，缺少订单号",
            channel: "通知内容",
            text: "您有一笔 ¥129.00 退款正在处理。\n本条通知未包含商家名称、订单号或到账凭据。"
        ),
        DemoSource(
            id: "neworder",
            title: "一张新的购物订单",
            hint: "收好订单，也保留后续入口",
            channel: "分享的页面",
            text: "暖光台灯 · ¥89.00\n商家：晚间生活馆\n订单号：DEMO-LAMP-1023\n已付款，等待发货\n下单日期：2026年10月23日"
        ),
        DemoSource(
            id: "movie",
            title: "明晚的电影票",
            hint: "已有同一张票，不会重复添加",
            channel: "选中的票据",
            text: "电影：流浪地球 3\n10月24日19:30 · 万达影城柳南店\n3号厅 · 7排8座\n票据编号：DEMO-MOVIE-1024"
        ),
        DemoSource(
            id: "wish",
            title: "一个散步的念头",
            hint: "认出已收下的同一个心愿",
            channel: "口述内容",
            text: "想去柳江边散散步，给自己留60分钟。"
        )
    ]

    static let memories: [MemoryItem] = [
        MemoryItem(
            id: "cinema",
            text: "这家影院空调冷，下次带件薄外套。",
            place: "万达影城 · 柳南店",
            date: "10月12日",
            source: "看完电影后手动记下：“这家影院空调冷，下次带件薄外套。”",
            enabled: true
        ),
        MemoryItem(
            id: "trip-note",
            text: "短途旅行先确认交通，再整理行李。",
            place: "旅行准备",
            date: "10月18日",
            source: "个人笔记：“酒店已经选好了，交通还没确认。确定出发时间后再收拾行李。”",
            enabled: true
        )
    ]
}
