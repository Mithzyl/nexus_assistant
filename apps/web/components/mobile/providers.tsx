"use client";

/**
 * Client-side providers: session, explicit demo data, browser-local records,
 * and the shell UI (theme, toast, capture sheet).
 *
 * The session token never enters this file: it stays in an httpOnly cookie and
 * the browser only sees identity/expiry through `/api/auth/*`.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ApiError,
  fetchSession,
  login as loginRequest,
  logoutSession,
  renewSession,
  type Me,
} from "@/lib/mobile/api";
import {
  INITIAL_LOGS,
  INITIAL_MATTERS,
  INITIAL_MEMORIES,
  type CaptureOutcome,
  type DemoLog,
  type DemoMatter,
  type DemoMemory,
} from "@/lib/mobile/demo";
import { uuid } from "@/lib/mobile/format";
import {
  accountKey,
  clearAccountCaches,
  forgetLastAccount,
  readJson,
  readLastAccount,
  rememberAccount,
  writeJson,
} from "@/lib/mobile/storage";
import { CaptureSheet } from "@/components/mobile/capture-sheet";

/* -------------------------------------------------------------------------- */
/* Auth                                                                       */
/* -------------------------------------------------------------------------- */

type AuthState = "loading" | "anonymous" | "signedIn" | "offline";

interface AuthValue {
  state: AuthState;
  user: Me | null;
  expiresAt: string | null;
  error: string | null;
  renewing: boolean;
  renewMessage: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<{ revoked: boolean }>;
  refresh: () => Promise<void>;
  renew: () => Promise<void>;
  clearSessionState: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>("loading");
  const [user, setUser] = useState<Me | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renewing, setRenewing] = useState(false);
  const [renewMessage, setRenewMessage] = useState<string | null>(null);
  const renewingRef = useRef(false);
  const lastUserRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await fetchSession();
      setUser(result.user);
      setExpiresAt(result.expires_at);
      setError(null);
      setState(result.user ? "signedIn" : "anonymous");
      if (result.user) rememberAccount(result.user.user_id);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        clearAccountCaches(user?.user_id ?? readLastAccount());
        forgetLastAccount();
        setUser(null);
        setExpiresAt(null);
        setState("anonymous");
        setError(caught.message || "会话已过期，请重新登录。");
      } else {
        setState(user ? "offline" : "offline");
        setError(caught instanceof Error ? caught.message : "登录状态暂时无法验证。");
      }
    }
  }, [user]);

  const renew = useCallback(async () => {
    if (renewingRef.current) return;
    renewingRef.current = true;
    setRenewing(true);
    setRenewMessage(null);
    try {
      const result = await renewSession();
      setUser(result.user);
      setExpiresAt(result.expires_at);
      setState("signedIn");
      rememberAccount(result.user.user_id);
      setRenewMessage("已续期");
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.status === 401) {
          clearAccountCaches(user?.user_id ?? readLastAccount());
          forgetLastAccount();
          setUser(null);
          setExpiresAt(null);
          setState("anonymous");
          setError(caught.message || "会话已过期，请重新登录。");
          setRenewMessage("登录状态已失效，请重新登录。");
        } else {
          setRenewMessage(caught.message);
        }
      } else {
        setRenewMessage("续期结果暂不确定，稍后会再核对。");
      }
    } finally {
      renewingRef.current = false;
      setRenewing(false);
    }
  }, [user]);

  useEffect(() => {
    void refresh();
    // Run once on mount; refresh itself is intentionally not a dependency here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (state !== "signedIn" && state !== "offline") return;
    const check = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (!expiresAt) {
        void renew();
        return;
      }
      const remaining = Date.parse(expiresAt) - Date.now();
      if (Number.isFinite(remaining) && remaining < 20 * 60 * 1000) {
        void renew();
      }
    };
    check();
    const timer = window.setInterval(check, 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [state, expiresAt, renew]);

  const login = useCallback(async (email: string, password: string) => {
    const result = await loginRequest(email.trim(), password);
    rememberAccount(result.user.user_id);
    setUser(result.user);
    setExpiresAt(result.expires_at);
    setState("signedIn");
    setError(null);
    setRenewMessage(null);
  }, []);

  const logout = useCallback(async () => {
    let revoked = false;
    try {
      const result = await logoutSession();
      revoked = result.revoked;
    } catch {
      revoked = false;
    }
    clearAccountCaches(user?.user_id ?? readLastAccount());
    forgetLastAccount();
    setUser(null);
    setExpiresAt(null);
    setState("anonymous");
    setRenewMessage(null);
    return { revoked };
  }, [user]);

  const clearSessionState = useCallback(() => {
    clearAccountCaches(user?.user_id ?? readLastAccount());
    forgetLastAccount();
    setUser(null);
    setExpiresAt(null);
    setState("anonymous");
  }, [user]);

  useEffect(() => {
    const next = user?.user_id ?? null;
    if (lastUserRef.current && lastUserRef.current !== next) {
      clearAccountCaches(lastUserRef.current);
    }
    lastUserRef.current = next;
  }, [user]);

  const value = useMemo<AuthValue>(
    () => ({
      state,
      user,
      expiresAt,
      error,
      renewing,
      renewMessage,
      login,
      logout,
      refresh,
      renew,
      clearSessionState,
    }),
    [
      state,
      user,
      expiresAt,
      error,
      renewing,
      renewMessage,
      login,
      logout,
      refresh,
      renew,
      clearSessionState,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/* -------------------------------------------------------------------------- */
/* Demo mode                                                                  */
/* -------------------------------------------------------------------------- */

interface DemoValue {
  enabled: boolean;
  setEnabled: (value: boolean) => void;
  matters: DemoMatter[];
  logs: DemoLog[];
  memories: DemoMemory[];
  pendingAmbiguity: boolean;
  ambiguityResolved: "coffee" | "shelf" | null;
  receiptUndone: boolean;
  checks: string[];
  wishScheduled: boolean;
  project: { transport: boolean; packing: boolean };
  travel: { imported: boolean; accepted: boolean; contacts: string[] };
  toggleCheck: (id: string) => void;
  scheduleWish: () => void;
  unscheduleWish: () => void;
  advanceProject: () => void;
  importTravel: () => void;
  acceptTravel: () => void;
  toggleContact: (id: string) => void;
  applyReceipt: () => void;
  undoReceipt: () => void;
  reapplyReceipt: () => void;
  resolveAmbiguity: (choice: "coffee" | "shelf") => void;
  addMemory: (text: string) => void;
  toggleMemory: (id: string) => void;
  updateMemory: (id: string, text: string) => void;
  captureDemo: (sourceId: string | null, text?: string) => CaptureOutcome;
}

const DemoContext = createContext<DemoValue | null>(null);

export function useDemo(): DemoValue {
  const value = useContext(DemoContext);
  if (!value) throw new Error("useDemo must be used inside DemoProvider");
  return value;
}

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabledState] = useState(false);
  const [matters, setMatters] = useState<DemoMatter[]>(INITIAL_MATTERS);
  const [logs, setLogs] = useState<DemoLog[]>(INITIAL_LOGS);
  const [memories, setMemories] = useState<DemoMemory[]>(INITIAL_MEMORIES);
  const [pendingAmbiguity, setPendingAmbiguity] = useState(true);
  const [ambiguityResolved, setAmbiguityResolved] = useState<
    "coffee" | "shelf" | null
  >(null);
  const [receiptUndone, setReceiptUndone] = useState(false);
  const [checks, setChecks] = useState<string[]>([]);
  const [wishScheduled, setWishScheduled] = useState(false);
  const [project, setProject] = useState({ transport: false, packing: false });
  const [travel, setTravel] = useState({
    imported: false,
    accepted: false,
    contacts: [] as string[],
  });

  useEffect(() => {
    setEnabledState(readJson("pa.web.demo.enabled", false));
  }, []);

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value);
    writeJson("pa.web.demo.enabled", value);
  }, []);

  const pushLog = useCallback((title: string, detail: string, target: string | null) => {
    setLogs((current) => [
      { id: uuid(), title, detail, target },
      ...current.filter((log) => log.title !== title),
    ].slice(0, 8));
  }, []);

  const updateRefund = useCallback(
    (id: string, stage: DemoMatter["refundStage"], status: DemoMatter["status"]) => {
      setMatters((current) =>
        current.map((matter) =>
          matter.id === id
            ? {
                ...matter,
                refundStage: stage,
                status,
                subtitle:
                  id === "coffee" ? "晨间生活店 · 退款跟进中" : "家居小店 · 退款跟进中",
              }
            : matter,
        ),
      );
    },
    [],
  );

  const applyReceipt = useCallback(() => {
    updateRefund("coffee", "received", "done");
    setReceiptUndone(false);
    pushLog("退款已按回执更新到账", "同一订单，没有多建一笔", "/matters/coffee");
  }, [pushLog, updateRefund]);

  const undoReceipt = useCallback(() => {
    updateRefund("coffee", "merchantInitiated", "active");
    setReceiptUndone(true);
    pushLog("撤销了这次到账更新", "恢复为商家已发起，仍保留回执依据", "/matters/coffee");
  }, [pushLog, updateRefund]);

  const reapplyReceipt = useCallback(() => {
    applyReceipt();
  }, [applyReceipt]);

  const resolveAmbiguity = useCallback(
    (choice: "coffee" | "shelf") => {
      setPendingAmbiguity(false);
      setAmbiguityResolved(choice);
      updateRefund(choice, "merchantInitiated", "active");
      pushLog(
        "确认了这笔退款的归属",
        `关联到${choice === "coffee" ? "便携咖啡机" : "收纳架"}，没有标记到账`,
        `/matters/${choice}`,
      );
    },
    [pushLog, updateRefund],
  );

  const toggleCheck = useCallback((id: string) => {
    setChecks((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }, []);

  const scheduleWish = useCallback(() => {
    setWishScheduled(true);
    setMatters((current) =>
      current.map((matter) =>
        matter.id === "wish"
          ? { ...matter, subtitle: "10月24日 15:00—16:00 · 已安排", nextStep: "到时出发" }
          : matter,
      ),
    );
    pushLog("为散步留出了一个小时", "10月24日 15:00—16:00，本地安排，不同步系统日历", "/matters/wish");
  }, [pushLog]);

  const unscheduleWish = useCallback(() => {
    setWishScheduled(false);
    setMatters((current) =>
      current.map((matter) =>
        matter.id === "wish"
          ? { ...matter, subtitle: "需要 60 分钟 · 还没安排", nextStep: "挑一个空档" }
          : matter,
      ),
    );
  }, []);

  const advanceProject = useCallback(() => {
    setProject((current) => {
      if (!current.transport) return { ...current, transport: true };
      if (!current.packing) return { ...current, packing: true };
      return current;
    });
  }, []);

  const importTravel = useCallback(() => {
    setTravel((current) => ({ ...current, imported: true }));
    setMatters((current) =>
      current.map((matter) =>
        matter.id === "travel"
          ? { ...matter, subtitle: "到达改到 14:00 · 2 处要确认", nextStep: "确认接站与活动" }
          : matter,
      ),
    );
  }, []);

  const acceptTravel = useCallback(() => {
    setTravel((current) => ({ ...current, accepted: true }));
    setMatters((current) =>
      current.map((matter) =>
        matter.id === "travel"
          ? { ...matter, nextStep: "等待你联系相关人员" }
          : matter,
      ),
    );
    pushLog("生成了 2 条待联系事项", "只生成清单，没有自动发送消息或改签", "/matters/travel");
  }, [pushLog]);

  const toggleContact = useCallback((id: string) => {
    setTravel((current) => ({
      ...current,
      contacts: current.contacts.includes(id)
        ? current.contacts.filter((item) => item !== id)
        : [...current.contacts, id],
    }));
  }, []);

  const addMemory = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setMemories((current) => [
        {
          id: `lesson-${Date.now()}`,
          text: trimmed,
          place: "手动记录",
          dateText: "刚刚",
          source: `你主动写下的一句话：“${trimmed}”`,
          enabled: true,
        },
        ...current,
      ]);
    },
    [],
  );

  const toggleMemory = useCallback((id: string) => {
    setMemories((current) =>
      current.map((memory) =>
        memory.id === id ? { ...memory, enabled: !memory.enabled } : memory,
      ),
    );
  }, []);

  const updateMemory = useCallback((id: string, text: string) => {
    setMemories((current) =>
      current.map((memory) => (memory.id === id ? { ...memory, text } : memory)),
    );
  }, []);

  const addUnknownMatter = useCallback(
    (text: string): DemoMatter => {
      const matter: DemoMatter = {
        id: `unknown-${Date.now()}`,
        kind: "unknown",
        title: text.slice(0, 18) || "待整理的一句话",
        subtitle: "待整理 · 未擅自补上日期、金额或待办",
        status: "active",
        amount: null,
        dateText: "刚刚",
        refundStage: null,
        evidenceId: null,
        nextStep: "由你确认它属于哪件事",
        detail: [
          { label: "收下时间", value: "刚刚" },
          { label: "来源", value: "本机演示的一句话" },
        ],
      };
      setMatters((current) => [matter, ...current]);
      pushLog("先收下，不替你猜", "这句话已保留，没有自动补日期、金额或行动", `/matters/${matter.id}`);
      return matter;
    },
    [pushLog],
  );

  const addLamp = useCallback((): DemoMatter => {
    const matter: DemoMatter = {
      id: "lamp",
      kind: "order",
      title: "暖光台灯",
      subtitle: "晚间生活馆 · 等待发货",
      status: "active",
      amount: "89.00",
      dateText: "10月23日",
      refundStage: null,
      evidenceId: "lamp",
      nextStep: "等待发货",
      detail: [
        { label: "订单号", value: "DEMO-LAMP-1023" },
        { label: "金额", value: "¥89.00" },
        { label: "商家", value: "晚间生活馆" },
      ],
    };
    setMatters((current) =>
      current.some((item) => item.id === "lamp") ? current : [matter, ...current],
    );
    pushLog("台灯订单收好了", "原文和订单号都保留，后续消息会归到同一件事", "/matters/lamp");
    return matter;
  }, [pushLog]);

  const captureDemo = useCallback(
    (sourceId: string | null, text = ""): CaptureOutcome => {
      const coffee = matters.find((matter) => matter.id === "coffee");
      if (sourceId === "notice") {
        if (coffee?.refundStage === "received") {
          pushLog("补充了退款依据", "同一订单的较早通知，不覆盖已有到账回执", "/matters/coffee");
          return {
            tone: "duplicate",
            title: "补充了依据，保留已到账状态",
            body: "同一订单的较早进度通知已归入原事务，它不会覆盖后来已有的到账回执。",
            route: "/matters/coffee",
          };
        }
        updateRefund("coffee", "merchantInitiated", "active");
        pushLog("退款通知已归入原订单", "商家已发起，但还没有到账", "/matters/coffee");
        return {
          tone: "done",
          title: "已更新原来的退款事务",
          body: "订单号和金额都一致。商家已发起，但还没有到账，所以状态停在“商家已发起”。",
          route: "/matters/coffee",
        };
      }
      if (sourceId === "receipt") {
        if (coffee?.refundStage === "received") {
          return {
            tone: "duplicate",
            title: "这份回执已经处理过",
            body: "同一份到账回执已归入原事务，不会再次改变状态。",
            route: "/matters/coffee",
          };
        }
        if (receiptUndone) {
          return {
            tone: "duplicate",
            title: "你撤销过这次更新，保持原样",
            body: "同一份回执已经处理过，而且你选择了撤销。不会因为重复交入而悄悄重新应用。",
            route: "/matters/coffee",
          };
        }
        applyReceipt();
        return {
          tone: "done",
          title: "这次到账，记下了",
          body: "订单号与金额都一致。回执作为到账依据保存，可随时撤销这次更新。",
          route: "/matters/coffee",
        };
      }
      if (sourceId === "ambiguous") {
        setPendingAmbiguity(true);
        return {
          tone: "unclear",
          title: "还差你确认一小步",
          body: "¥129 对应两件正在退款的商品。通知没有订单号，先不关联，也不会标记到账。",
          route: "/matters/ambiguity",
        };
      }
      if (sourceId === "neworder") {
        if (matters.some((matter) => matter.id === "lamp")) {
          return {
            tone: "duplicate",
            title: "这个订单已经在这里了",
            body: "识别到相同的订单号，继续使用原来的事务，没有重复收下。",
            route: "/matters/lamp",
          };
        }
        addLamp();
        return {
          tone: "done",
          title: "台灯订单收好了",
          body: "原文和订单号都保留。以后有物流或售后消息，会归到同一件事。",
          route: "/matters/lamp",
        };
      }
      if (sourceId === "movie") {
        return {
          tone: "duplicate",
          title: "已经有了，没有重复收下",
          body: "同样的电影票已经收好，继续使用原来的票据事务。",
          route: "/matters/movie",
        };
      }
      if (sourceId === "wish") {
        return {
          tone: "duplicate",
          title: "这个心愿，已经在这里了",
          body: "认出了“柳江边散步”。保留已有心愿，不重复创建；你可以接着为它找时间。",
          route: "/matters/wish",
        };
      }
      if (sourceId) {
        return {
          tone: "duplicate",
          title: "已经收好了",
          body: "这类材料会继续沿用原来的事务。",
          route: "/matters",
        };
      }

      const trimmed = text.trim();
      if (!trimmed) {
        return {
          tone: "unclear",
          title: "先写一句再交过来",
          body: "没有内容时不会生成任何结果。",
          route: null,
        };
      }
      if (/柳江|散步/.test(trimmed)) {
        return {
          tone: "duplicate",
          title: "这个心愿，已经在这里了",
          body: "认出了“柳江边散步”。保留已有心愿，不重复创建；你可以接着为它找时间。",
          route: "/matters/wish",
        };
      }
      if (/下次|记得|外套|经验/.test(trimmed)) {
        addMemory(trimmed);
        pushLog("这句经验，替你收好了", "来自你主动写下的内容，可以随时停用", "/me/memories");
        return {
          tone: "done",
          title: "这句经验，替你收好了",
          body: "放进了个人记忆。它不是凭空生成的结论，你可以随时修改或停用。",
          route: "/me/memories",
        };
      }
      const matter = addUnknownMatter(trimmed);
      return {
        tone: "unclear",
        title: "先收下，不替你猜",
        body: "这句话已保留在事务里。当前示例无法可靠判断更多信息，所以没有自动设置日期、金额或待办。",
        route: `/matters/${matter.id}`,
      };
    },
    [
      addLamp,
      addMemory,
      addUnknownMatter,
      applyReceipt,
      matters,
      pushLog,
      receiptUndone,
      updateRefund,
    ],
  );

  const value = useMemo<DemoValue>(
    () => ({
      enabled,
      setEnabled,
      matters,
      logs,
      memories,
      pendingAmbiguity,
      ambiguityResolved,
      receiptUndone,
      checks,
      wishScheduled,
      project,
      travel,
      toggleCheck,
      scheduleWish,
      unscheduleWish,
      advanceProject,
      importTravel,
      acceptTravel,
      toggleContact,
      applyReceipt,
      undoReceipt,
      reapplyReceipt,
      resolveAmbiguity,
      addMemory,
      toggleMemory,
      updateMemory,
      captureDemo,
    }),
    [
      enabled,
      setEnabled,
      matters,
      logs,
      memories,
      pendingAmbiguity,
      ambiguityResolved,
      receiptUndone,
      checks,
      wishScheduled,
      project,
      travel,
      toggleCheck,
      scheduleWish,
      unscheduleWish,
      advanceProject,
      importTravel,
      acceptTravel,
      toggleContact,
      applyReceipt,
      undoReceipt,
      reapplyReceipt,
      resolveAmbiguity,
      addMemory,
      toggleMemory,
      updateMemory,
      captureDemo,
    ],
  );

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

/* -------------------------------------------------------------------------- */
/* Browser-local records                                                      */
/* -------------------------------------------------------------------------- */

export interface LocalRecord {
  id: string;
  kind: "note" | "capture";
  title: string;
  text: string;
  createdAt: string;
}

interface LocalRecordsValue {
  records: LocalRecord[];
  addRecord: (record: Omit<LocalRecord, "id" | "createdAt">) => LocalRecord;
  removeRecord: (id: string) => void;
}

const LocalRecordsContext = createContext<LocalRecordsValue | null>(null);

export function useLocalRecords(): LocalRecordsValue {
  const value = useContext(LocalRecordsContext);
  if (!value) throw new Error("useLocalRecords must be used inside LocalProvider");
  return value;
}

export function LocalProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const key = accountKey("pa.web.records", user?.user_id || null);
  const [records, setRecords] = useState<LocalRecord[]>([]);

  useEffect(() => {
    setRecords(readJson<LocalRecord[]>(key, []));
  }, [key]);

  const persist = useCallback(
    (next: LocalRecord[]) => {
      setRecords(next);
      writeJson(key, next);
    },
    [key],
  );

  const addRecord = useCallback(
    (record: Omit<LocalRecord, "id" | "createdAt">) => {
      const created: LocalRecord = {
        ...record,
        id: uuid(),
        createdAt: new Date().toISOString(),
      };
      persist([created, ...records].slice(0, 50));
      return created;
    },
    [persist, records],
  );

  const removeRecord = useCallback(
    (id: string) => persist(records.filter((record) => record.id !== id)),
    [persist, records],
  );

  const value = useMemo(
    () => ({ records, addRecord, removeRecord }),
    [records, addRecord, removeRecord],
  );

  return (
    <LocalRecordsContext.Provider value={value}>
      {children}
    </LocalRecordsContext.Provider>
  );
}

/* -------------------------------------------------------------------------- */
/* Shell UI: theme, toast, capture sheet                                      */
/* -------------------------------------------------------------------------- */

type ThemeChoice = "system" | "light" | "dark";

interface UIValue {
  theme: ThemeChoice;
  setTheme: (theme: ThemeChoice) => void;
  toast: (message: string) => void;
  openCapture: (preset?: "choose" | "say") => void;
  closeCapture: () => void;
}

const UIContext = createContext<UIValue | null>(null);

export function useUI(): UIValue {
  const value = useContext(UIContext);
  if (!value) throw new Error("useUI must be used inside UIProvider");
  return value;
}

export function UIProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>("system");
  const [message, setMessage] = useState<string | null>(null);
  const [capture, setCapture] = useState<{ open: boolean; preset: "choose" | "say" }>({
    open: false,
    preset: "choose",
  });
  const timer = useRef<number | null>(null);

  useEffect(() => {
    const stored = readJson<ThemeChoice>("pa.web.theme.choice", "system");
    setThemeState(stored);
    if (stored === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", stored);
    }
  }, []);

  const setTheme = useCallback((next: ThemeChoice) => {
    setThemeState(next);
    writeJson("pa.web.theme.choice", next);
    if (next === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", next);
    }
  }, []);

  const toast = useCallback((next: string) => {
    setMessage(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(null), 2600);
  }, []);

  const openCapture = useCallback((preset: "choose" | "say" = "choose") => {
    setCapture({ open: true, preset });
  }, []);

  const closeCapture = useCallback(() => {
    setCapture((current) => ({ ...current, open: false }));
  }, []);

  const value = useMemo(
    () => ({ theme, setTheme, toast, openCapture, closeCapture }),
    [theme, setTheme, toast, openCapture, closeCapture],
  );

  return (
    <UIContext.Provider value={value}>
      {children}
      {message ? (
        <div className="toast-wrap" role="status" aria-live="polite">
          <div className="toast">{message}</div>
        </div>
      ) : null}
      {capture.open ? (
        <CaptureSheet preset={capture.preset} onClose={closeCapture} />
      ) : null}
    </UIContext.Provider>
  );
}

/* -------------------------------------------------------------------------- */

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <DemoProvider>
        <LocalProvider>
          <UIProvider>{children}</UIProvider>
        </LocalProvider>
      </DemoProvider>
    </AuthProvider>
  );
}
