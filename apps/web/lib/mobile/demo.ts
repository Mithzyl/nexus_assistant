/**
 * Central demo data and types.
 *
 * Demo content is never mixed into a real (non-demo) session: it is only shown
 * after the user explicitly enables demo mode on the Me tab, and every screen
 * carrying it renders the "演示模式" banner.
 */

export type MatterKind =
  | "refund"
  | "order"
  | "ticket"
  | "wish"
  | "project"
  | "travel"
  | "unknown";

export type RefundStage = "requested" | "merchantInitiated" | "received";

export interface DemoMatter {
  id: string;
  kind: MatterKind;
  title: string;
  subtitle: string;
  status: "active" | "done";
  amount: string | null;
  dateText: string;
  refundStage: RefundStage | null;
  evidenceId: string | null;
  nextStep: string | null;
  detail: Array<{ label: string; value: string }>;
}

export interface DemoEvidence {
  id: string;
  title: string;
  meta: string;
  text: string;
}

export interface DemoMemory {
  id: string;
  text: string;
  place: string;
  dateText: string;
  source: string;
  enabled: boolean;
}

export interface DemoLog {
  id: string;
  title: string;
  detail: string;
  target: string | null;
}

export interface DemoSource {
  id: string;
  title: string;
  hint: string;
  channel: string;
  text: string;
}

export interface CaptureOutcome {
  tone: "done" | "duplicate" | "unclear";
  title: string;
  body: string;
  route: string | null;
}

export const MATTER_KIND_LABEL: Record<MatterKind, string> = {
  refund: "退款",
  order: "订单",
  ticket: "票据",
  wish: "心愿",
  project: "项目",
  travel: "行程",
  unknown: "待整理",
};

export const REFUND_STAGE_LABEL: Record<RefundStage, string> = {
  requested: "已申请",
  merchantInitiated: "商家已发起",
  received: "已到账",
};

export const INITIAL_MATTERS: DemoMatter[] = [
  {
    id: "coffee",
    kind: "refund",
    title: "便携咖啡机",
    subtitle: "晨间生活店 · 已申请退款",
    status: "active",
    amount: "129.00",
    dateText: "10月17日",
    refundStage: "requested",
    evidenceId: "order",
    nextStep: "等待商家处理",
    detail: [
      { label: "订单号", value: "DEMO-20261015-129" },
      { label: "申请退款", value: "10月17日 · ¥129.00" },
      { label: "商家", value: "晨间生活店" },
    ],
  },
  {
    id: "shelf",
    kind: "refund",
    title: "收纳架",
    subtitle: "家居小店 · 已申请退款",
    status: "active",
    amount: "129.00",
    dateText: "10月20日",
    refundStage: "requested",
    evidenceId: "shelfOrder",
    nextStep: "等待商家处理",
    detail: [
      { label: "订单号", value: "DEMO-SHELF-1020" },
      { label: "申请退款", value: "10月20日 · ¥129.00" },
      { label: "商家", value: "家居小店" },
    ],
  },
  {
    id: "movie",
    kind: "ticket",
    title: "流浪地球 3",
    subtitle: "明晚 19:30 · 万达影城柳南店",
    status: "active",
    amount: null,
    dateText: "10月24日",
    refundStage: null,
    evidenceId: "ticket",
    nextStep: "出门前检查清单",
    detail: [
      { label: "时间", value: "10月24日 19:30" },
      { label: "影厅 / 座位", value: "3号厅 / 7排8座" },
      { label: "取票码", value: "DEMO 4826 0318" },
    ],
  },
  {
    id: "wish",
    kind: "wish",
    title: "去柳江边散散步",
    subtitle: "需要 60 分钟 · 还没安排",
    status: "active",
    amount: null,
    dateText: "10月21日",
    refundStage: null,
    evidenceId: "wish",
    nextStep: "挑一个空档",
    detail: [
      { label: "预计时长", value: "60 分钟" },
      { label: "可用时间", value: "10月24日 15:00—17:00（手动录入）" },
    ],
  },
  {
    id: "project",
    kind: "project",
    title: "周末短途旅行准备",
    subtitle: "3 步里完成 1 步",
    status: "active",
    amount: null,
    dateText: "10月18日",
    refundStage: null,
    evidenceId: "project",
    nextStep: "确认交通",
    detail: [
      { label: "上次停在这里", value: "10月18日" },
      { label: "已完成", value: "酒店已选好（尚未代订）" },
    ],
  },
  {
    id: "travel",
    kind: "travel",
    title: "行程改期影响",
    subtitle: "10月26日 · 到达时间待核对",
    status: "active",
    amount: null,
    dateText: "10月22日",
    refundStage: null,
    evidenceId: "travel",
    nextStep: "核对到达时间",
    detail: [
      { label: "原定到达", value: "11:00" },
      { label: "原定接站", value: "11:30" },
      { label: "原定活动", value: "13:00" },
    ],
  },
];

export const EVIDENCE: Record<string, DemoEvidence> = {
  order: {
    id: "order",
    title: "咖啡机订单",
    meta: "订单页面 · 示例",
    text: "订单号 DEMO-20261015-129\n商品：便携咖啡机\n金额：¥129.00\n商家：晨间生活店\n下单时间：10月15日 09:12\n状态：已签收",
  },
  shelfOrder: {
    id: "shelfOrder",
    title: "收纳架订单",
    meta: "订单页面 · 示例",
    text: "订单号 DEMO-SHELF-1020\n商品：收纳架\n金额：¥129.00\n商家：家居小店\n下单时间：10月20日 20:41\n状态：已签收",
  },
  notice: {
    id: "notice",
    title: "退款进度通知",
    meta: "通知内容 · 示例",
    text: "【晨间生活店】您的退款 ¥129.00 商家已发起，预计 1—3 个工作日退回原支付账户。\n本通知不包含订单号。",
  },
  receipt: {
    id: "receipt",
    title: "退款到账回执",
    meta: "选中的回执 · 示例",
    text: "退款到账回执\n订单号：DEMO-20261015-129\n退款金额：¥129.00\n状态：已退回原账户\n到账时间：10月23日 16:08",
  },
  ambiguous: {
    id: "ambiguous",
    title: "一条不完整的退款通知",
    meta: "通知内容 · 示例",
    text: "退款通知：您有一笔 ¥129.00 的退款正在处理。\n（没有订单号、商家名称或到账状态）",
  },
  ticket: {
    id: "ticket",
    title: "电影票",
    meta: "选中的票据 · 示例",
    text: "电影：流浪地球 3\n影院：万达影城 · 柳南店\n影厅 / 座位：3号厅 / 7排8座\n时间：10月24日 19:30\n取票码：DEMO 4826 0318\n此票为虚构示例，不可用于真实取票。",
  },
  wish: {
    id: "wish",
    title: "散步的心愿",
    meta: "口述内容 · 示例",
    text: "想去柳江边散散步，留 60 分钟。没有查询天气或开放情况。",
  },
  project: {
    id: "project",
    title: "上次的决定和笔记",
    meta: "项目笔记 · 示例",
    text: "周末短途旅行准备\n1. 酒店已选好：江景客栈，保留选择，尚未代订。\n2. 交通未确认。\n3. 行李还没整理。",
  },
  travel: {
    id: "travel",
    title: "原行程与示例通知",
    meta: "行程记录 · 示例",
    text: "原行程：10月26日 11:00 到达，11:30 接站，13:00 活动。\n示例通知：列车改点，预计 14:00 到达。\n原安排未被自动改写。",
  },
  lamp: {
    id: "lamp",
    title: "暖光台灯订单",
    meta: "分享的页面 · 示例",
    text: "订单号 DEMO-LAMP-1023\n商品：暖光台灯\n金额：¥89.00\n商家：晚间生活馆\n状态：等待发货",
  },
};

export const INITIAL_LOGS: DemoLog[] = [
  {
    id: "log-ticket",
    title: "电影票已归好",
    detail: "关联到明晚的电影，出门前会提醒带票据",
    target: "/matters/movie",
  },
  {
    id: "log-order",
    title: "咖啡机订单已收好",
    detail: "原订单与退款通知留在同一件事务里",
    target: "/matters/coffee",
  },
  {
    id: "log-wish",
    title: "把散步的念头留住了",
    detail: "心愿已保存，还没安排时间",
    target: "/matters/wish",
  },
];

export const INITIAL_MEMORIES: DemoMemory[] = [
  {
    id: "cinema",
    text: "7排中间视野很好，下次还可以选这里。",
    place: "万达影城 · 柳南店",
    dateText: "10月12日",
    source: "手动记录的体验：“7排中间视野很好。”",
    enabled: true,
  },
  {
    id: "river",
    text: "江边傍晚风大，记得带件薄外套。",
    place: "柳江边",
    dateText: "10月18日",
    source: "手动记录的体验：“江边傍晚风大。”",
    enabled: true,
  },
];

export const DEMO_SOURCES: DemoSource[] = [
  {
    id: "notice",
    title: "退款进度通知",
    hint: "更新已有退款，不当作已到账",
    channel: "通知内容",
    text: EVIDENCE.notice.text,
  },
  {
    id: "receipt",
    title: "退款到账回执",
    hint: "金额、订单号一致，可直接关联",
    channel: "选中的回执",
    text: EVIDENCE.receipt.text,
  },
  {
    id: "ambiguous",
    title: "一条不完整的退款通知",
    hint: "只有¥129，缺少订单号",
    channel: "通知内容",
    text: EVIDENCE.ambiguous.text,
  },
  {
    id: "neworder",
    title: "一张新的购物订单",
    hint: "收好订单，也保留后续入口",
    channel: "分享的页面",
    text: EVIDENCE.lamp.text,
  },
  {
    id: "movie",
    title: "明晚的电影票",
    hint: "已有同一张票，不会重复添加",
    channel: "选中的票据",
    text: EVIDENCE.ticket.text,
  },
  {
    id: "wish",
    title: "一个散步的念头",
    hint: "认出已收下的同一个心愿",
    channel: "口述内容",
    text: EVIDENCE.wish.text,
  },
];

export const MATTER_IDS = new Set(INITIAL_MATTERS.map((matter) => matter.id));
