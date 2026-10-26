/**
 * Local-only preview of the work-planning model.
 *
 * The backend has no matter/schedule/reminder write API yet, so this data is
 * deliberately browser-local and must always be labelled as a preview. Never
 * treat it as a server-saved matter or as an iOS calendar write.
 */

export type WorkMatterStatus = "active" | "waiting" | "archived";
export type WorkSyncStatus = "local_only" | "pending_ios" | "ios_synced";

export interface WorkSchedule {
  id: string;
  title: string;
  startsAt: string;
  endsAt: string;
  location: string;
  source: "web_native";
  syncStatus: WorkSyncStatus;
}

export interface WorkTodo {
  id: string;
  title: string;
  dueAt: string | null;
  reminderAt: string | null;
  done: boolean;
}

export interface WorkHistory {
  id: string;
  at: string;
  text: string;
}

export interface WorkMatter {
  id: string;
  title: string;
  goal: string;
  status: WorkMatterStatus;
  progress: string;
  nextAction: string;
  nextAt: string | null;
  schedules: WorkSchedule[];
  todos: WorkTodo[];
  history: WorkHistory[];
  stopped?: boolean;
}

export interface WorkConflictOption {
  id: string;
  title: string;
  detail: string;
}

export interface WorkConflict {
  matterId: string;
  matterTitle: string;
  first: WorkSchedule;
  second: WorkSchedule;
  gapMinutes: number;
  travelMinutes: number;
  options: WorkConflictOption[];
}

export const WORK_STATUS_LABEL: Record<WorkMatterStatus, string> = {
  active: "正在推进",
  waiting: "等待外部反馈",
  archived: "已完成／归档",
};

export const SYNC_STATUS_LABEL: Record<WorkSyncStatus, string> = {
  local_only: "应用内预览 · 未写入服务器",
  pending_ios: "待 iOS 客户端同步",
  ios_synced: "iOS 已同步",
};

export function localDateKey(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

export function todayKey(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate(),
  ).padStart(2, "0")}`;
}

export function atLocal(dayOffset: number, hour: number, minute: number): string {
  const date = new Date();
  date.setDate(date.getDate() + dayOffset);
  date.setHours(hour, minute, 0, 0);
  return date.toISOString();
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function dateTimeLabel(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function statusOf(matter: WorkMatter): WorkMatterStatus {
  return matter.status;
}

export function seedWorkMatters(): WorkMatter[] {
  const history = (id: string, hoursAgo: number, text: string): WorkHistory => ({
    id,
    at: atLocal(-Math.ceil(hoursAgo / 24), 18, 0),
    text,
  });
  return [
    {
      id: "wp-lin",
      title: "与林总谈年度合作合同",
      goal: "把年度合作条款谈定，并拿回双方签回的合同。",
      status: "active",
      progress: "法务修订版还没有收到；面谈时间已定在周四下午。",
      nextAction: "明天 18:00 前催法务发修订版",
      nextAt: atLocal(1, 18, 0),
      schedules: [
        {
          id: "wp-lin-dentist",
          title: "静安寺洗牙",
          startsAt: atLocal(0, 13, 30),
          endsAt: atLocal(0, 14, 50),
          location: "静安寺",
          source: "web_native",
          syncStatus: "local_only",
        },
        {
          id: "wp-lin-meeting",
          title: "陆家嘴面谈",
          startsAt: atLocal(0, 15, 0),
          endsAt: atLocal(0, 16, 0),
          location: "陆家嘴",
          source: "web_native",
          syncStatus: "local_only",
        },
      ],
      todos: [
        {
          id: "wp-lin-legal",
          title: "催法务发合同修订版",
          dueAt: atLocal(1, 18, 0),
          reminderAt: atLocal(1, 17, 30),
          done: false,
        },
        {
          id: "wp-lin-print",
          title: "打印两份盖章页",
          dueAt: atLocal(0, 12, 30),
          reminderAt: null,
          done: false,
        },
      ],
      history: [
        history("wp-lin-h1", 1, "和林总确认周四 15:00 在陆家嘴见面。"),
        history("wp-lin-h2", 3, "把合同初稿发给法务，请他们出修订版。"),
      ],
    },
    {
      id: "wp-vendor",
      title: "供应商续约",
      goal: "在月底前完成年度框架协议的续签。",
      status: "waiting",
      progress: "等对方回传盖章版；条款已经双方确认。",
      nextAction: "周五前跟进盖章进度",
      nextAt: atLocal(3, 10, 0),
      schedules: [
        {
          id: "wp-vendor-call",
          title: "供应商电话确认",
          startsAt: atLocal(2, 10, 0),
          endsAt: atLocal(2, 10, 30),
          location: "线上",
          source: "web_native",
          syncStatus: "local_only",
        },
      ],
      todos: [
        {
          id: "wp-vendor-reply",
          title: "收到盖章版后归档",
          dueAt: atLocal(3, 10, 0),
          reminderAt: null,
          done: false,
        },
      ],
      history: [history("wp-vendor-h1", 2, "对方确认条款无误，走内部盖章。")],
    },
    {
      id: "wp-budget",
      title: "Q4 预算复盘",
      goal: "复盘 Q4 预算偏差，形成下季度调整建议。",
      status: "archived",
      progress: "复盘会已完成，结论已发给财务。",
      nextAction: "无",
      nextAt: null,
      schedules: [],
      todos: [
        {
          id: "wp-budget-send",
          title: "发送复盘结论",
          dueAt: atLocal(-1, 18, 0),
          reminderAt: null,
          done: true,
        },
      ],
      history: [history("wp-budget-h1", 5, "复盘会完成，结论已发出。")],
    },
  ];
}

export function findConflict(matter: WorkMatter, travelMinutes = 25): WorkConflict | null {
  const ordered = [...matter.schedules].sort(
    (left, right) => new Date(left.startsAt).getTime() - new Date(right.startsAt).getTime(),
  );
  for (let index = 0; index < ordered.length - 1; index += 1) {
    const first = ordered[index];
    const second = ordered[index + 1];
    const gap = Math.round(
      (new Date(second.startsAt).getTime() - new Date(first.endsAt).getTime()) / 60000,
    );
    if (gap < 15) {
      return {
        matterId: matter.id,
        matterTitle: matter.title,
        first,
        second,
        gapMinutes: gap,
        travelMinutes,
        options: [
          {
            id: "shorten",
            title: "提前结束洗牙",
            detail: `把「${first.title}」提前 20 分钟结束，留出 ${travelMinutes} 分钟路上时间。`,
          },
          {
            id: "move",
            title: "面谈改到 15:30",
            detail: "保留洗牙安排，把「陆家嘴面谈」整体顺延 30 分钟。",
          },
          {
            id: "online",
            title: "改为线上参会",
            detail: "不移动洗牙，把面谈改为线上；地点和到场要求需另行确认。",
          },
        ],
      };
    }
  }
  return null;
}

export function applyConflictOption(
  matters: WorkMatter[],
  conflict: WorkConflict,
  optionId: string,
): WorkMatter[] {
  return matters.map((matter) => {
    if (matter.id !== conflict.matterId) return matter;
    const schedules = matter.schedules.map((schedule) => {
      if (optionId === "shorten" && schedule.id === conflict.first.id) {
        return {
          ...schedule,
          endsAt: new Date(new Date(schedule.endsAt).getTime() - 20 * 60000).toISOString(),
        };
      }
      if (optionId === "move" && schedule.id === conflict.second.id) {
        return {
          ...schedule,
          startsAt: new Date(new Date(schedule.startsAt).getTime() + 30 * 60000).toISOString(),
          endsAt: new Date(new Date(schedule.endsAt).getTime() + 30 * 60000).toISOString(),
        };
      }
      if (optionId === "online" && schedule.id === conflict.second.id) {
        return { ...schedule, title: `${schedule.title}（线上）`, location: "线上" };
      }
      return schedule;
    });
    const chosen = conflict.options.find((option) => option.id === optionId);
    return {
      ...matter,
      schedules,
      progress: chosen ? `已按「${chosen.title}」调整预览安排，等待确认后生效。` : matter.progress,
      history: chosen
        ? [
            {
              id: `hist-${Date.now()}`,
              at: new Date().toISOString(),
              text: `预览调整：${chosen.title}。${chosen.detail}`,
            },
            ...matter.history,
          ]
        : matter.history,
    };
  });
}
