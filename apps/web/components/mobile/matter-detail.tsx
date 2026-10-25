"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, FileText, MapPin, RotateCcw, Sparkles } from "lucide-react";

import {
  EVIDENCE,
  MATTER_KIND_LABEL,
  type DemoMatter,
} from "@/lib/mobile/demo";
import { useDemo, useUI } from "@/components/mobile/providers";
import { EmptyState, LinkRow, Notice, PageShell, Pill } from "@/components/mobile/ui";

function evidenceFor(matter: DemoMatter): string | null {
  return matter.evidenceId;
}

function RefundBody({ matter }: { matter: DemoMatter }) {
  const demo = useDemo();
  const { toast } = useUI();
  const stage = matter.refundStage ?? "requested";
  const received = stage === "received";
  const merchant = stage === "merchantInitiated" || received;
  const evidence = evidenceFor(matter);

  return (
    <>
      <div className={`card ${received ? "card--green" : "card--amber"}`}>
        <p className="micro" style={{ color: received ? "var(--m-green)" : "var(--m-amber)" }}>
          {received
            ? "回执已确认到账"
            : merchant
              ? "商家已发起退款"
              : "等待退款后续"}
        </p>
        <p
          className="tabular"
          style={{ margin: "6px 0 0", fontSize: 32, fontWeight: 600, letterSpacing: "-0.01em" }}
        >
          ¥{matter.amount}
        </p>
        <p className="caption" style={{ margin: "4px 0 0" }}>
          {received
            ? "10月23日 · 按匹配回执更新，仍保留原订单"
            : merchant
              ? "订单号匹配 · 仍需到账凭据"
              : `${matter.dateText}申请 · 只有申请记录，没有到账凭据`}
        </p>
      </div>

      <div className="card mt-14">
        <div className="timeline">
          <div className="timeline__item" data-done="true">
            <div className="timeline__rail">
              <span className="timeline__dot" />
              <span className="timeline__line" />
            </div>
            <div className="timeline__body">
              <strong>订单已保存</strong>
              <small>10月15日 · {matter.subtitle.split(" · ")[0]}</small>
            </div>
          </div>
          <div className="timeline__item" data-done={merchant ? "true" : "false"}>
            <div className="timeline__rail">
              <span className="timeline__dot" />
              <span className="timeline__line" />
            </div>
            <div className="timeline__body">
              <strong>{merchant ? "商家已发起退款" : "已申请退款"}</strong>
              <small>{merchant ? "已关联退款通知 · 还不是到账" : "10月17日 · 申请退回¥129"}</small>
            </div>
          </div>
          <div className="timeline__item" data-done={received ? "true" : "false"}>
            <div className="timeline__rail">
              <span className="timeline__dot" />
            </div>
            <div className="timeline__body">
              <strong>{received ? "到账状态已更新" : "等待明确的到账依据"}</strong>
              <small>
                {received
                  ? "回执与原事务关联，保留依据供你核对"
                  : "仅有退款通知，不会提前标记完成"}
              </small>
            </div>
          </div>
        </div>
      </div>

      {!received && evidence === "order" ? (
        <div className="mt-14">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => {
              demo.applyReceipt();
              toast("已按回执更新为已到账");
            }}
          >
            演示：把到账回执交给助手 <ArrowRight size={14} />
          </button>
          <p className="micro text-center mt-6">演示操作 · 只会更新本地示例，不会读取银行或订单服务</p>
        </div>
      ) : null}

      {received && evidence === "order" ? (
        <>
          <div className="notice notice--green mt-14">
            <strong>同一件退款已完成，没有多建一笔。</strong>
            <p style={{ margin: "4px 0 0" }}>到账依据保留在事务里，可以撤销这次更新。</p>
          </div>
          <button
            type="button"
            className="btn btn--secondary mt-10"
            onClick={() => {
              demo.undoReceipt();
              toast("已撤销到账更新，恢复为商家已发起");
            }}
          >
            <RotateCcw size={14} /> 撤销这次到账更新
          </button>
        </>
      ) : null}

      {demo.receiptUndone && evidence === "order" ? (
        <Notice tone="amber" title="已撤销到账更新">
          恢复为之前的跟进状态。
          <button
            type="button"
            className="link-button link-button--inline"
            style={{ marginLeft: 6, color: "var(--m-amber)" }}
            onClick={() => {
              demo.reapplyReceipt();
              toast("已重新应用这份回执");
            }}
          >
            重新应用这份回执
          </button>
        </Notice>
      ) : null}

      {merchant && !received ? (
        <Notice tone="amber" title="商家已发起 ≠ 已到账">
          “已发起”只说明商家提交了退款，不等于钱已经回到账户。到账需要回执或其他明确依据。
        </Notice>
      ) : null}

      {evidence ? (
        <div className="mt-14">
          <Link href={`/matters/evidence/${evidence}`} className="link-button">
            <FileText size={14} /> 查看原始依据（{EVIDENCE[evidence]?.title}）
          </Link>
        </div>
      ) : null}
    </>
  );
}

function TicketBody({ matter }: { matter: DemoMatter }) {
  const demo = useDemo();
  const ready = demo.checks.includes("ticket");
  return (
    <>
      <div className="card">
        <p className="micro">电影电子票 · 10月24日</p>
        <p className="serif" style={{ margin: "6px 0 0", fontSize: 26, fontWeight: 600 }}>
          {matter.title}
        </p>
        <p className="tabular" style={{ margin: "2px 0 0", fontSize: 30, fontWeight: 600 }}>
          19:30
        </p>
        <div className="mt-12" style={{ display: "grid", gap: 8 }}>
          <div>
            <span className="micro" style={{ display: "block" }}>影院</span>
            <span style={{ fontSize: 15 }}>万达影城 · 柳南店</span>
          </div>
          <div>
            <span className="micro" style={{ display: "block" }}>影厅 / 座位</span>
            <span style={{ fontSize: 15 }}>3号厅 / 7排8座</span>
          </div>
          <div>
            <span className="micro" style={{ display: "block" }}>示例取票码</span>
            <span className="mono" style={{ fontSize: 18, fontWeight: 600 }}>
              DEMO 4826 0318
            </span>
          </div>
        </div>
      </div>
      <div className="mt-14">
        <Link href="/matters/prepare" className="btn btn--primary">
          打开出门检查清单 <ArrowRight size={14} />
        </Link>
      </div>
      <button
        type="button"
        className="btn btn--secondary mt-10"
        onClick={() => demo.toggleCheck("ticket")}
      >
        {ready ? <Check size={14} /> : null}
        {ready ? "已备好票据" : "票据已备好"}
      </button>
      <Notice>这是虚构示例票据，不可用于真实取票；勾选只记录在本地清单里。</Notice>
      <Link href="/matters/evidence/ticket" className="link-button mt-6">
        <FileText size={14} /> 查看原始票据
      </Link>
    </>
  );
}

function WishBody({ matter }: { matter: DemoMatter }) {
  const demo = useDemo();
  return (
    <>
      <div className="card">
        <p className="micro">你的心愿 · 预计 60 分钟</p>
        <p className="serif" style={{ margin: "6px 0 0", fontSize: 24, fontWeight: 600 }}>
          {matter.title}
        </p>
        <p className="caption mt-6">想做的事，不必一直停在收藏里。</p>
        <div className="notice mt-12">
          <strong>可用时间（手动录入）</strong>
          <p style={{ margin: "4px 0 0" }}>10月24日 15:00—17:00，共 120 分钟空闲窗口。</p>
        </div>
      </div>

      {demo.wishScheduled ? (
        <>
          <div className="card card--green mt-14">
            <p className="flex items-center gap-8" style={{ margin: 0 }}>
              <Check size={15} className="green" />
              <strong style={{ fontSize: 15 }}>10月24日 15:00—16:00</strong>
            </p>
            <p className="caption mt-4" style={{ marginBottom: 0 }}>
              你已确认 · 已加入今天和事务（本地示例）
            </p>
          </div>
          <button
            type="button"
            className="btn btn--secondary mt-10"
            onClick={demo.unscheduleWish}
          >
            取消这次安排，保留心愿
          </button>
          <Notice tone="amber">天气与开放情况待核实；本地安排不会写入系统日历。</Notice>
        </>
      ) : (
        <>
          <div className="notice mt-14">
            天气与开放情况待核实；没有自动查询或外部预订，也不会同步到系统日历。
          </div>
          <button
            type="button"
            className="btn btn--primary mt-10"
            onClick={demo.scheduleWish}
          >
            <Sparkles size={14} /> 安排到明天 15:00
          </button>
        </>
      )}
      <Link href="/matters/evidence/wish" className="link-button mt-6">
        <FileText size={14} /> 查看心愿与空闲时间依据
      </Link>
    </>
  );
}

function ProjectBody() {
  const demo = useDemo();
  const steps = 1 + (demo.project.transport ? 1 : 0) + (demo.project.packing ? 1 : 0);
  return (
    <>
      <div className="card">
        <span className="micro">上次停在这里 · 10月18日</span>
        <h2 className="serif mt-4" style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
          周末短途旅行准备
        </h2>
        <p className="caption mt-4">不用重新想一遍，接着下一步就好。</p>
        <div className="flex items-center gap-10 mt-12">
          <span className="tabular" style={{ fontSize: 22, fontWeight: 600 }}>
            {steps}
            <span className="micro" style={{ fontSize: 14 }}>/3</span>
          </span>
          <span className="caption" style={{ margin: 0 }}>准备已完成</span>
        </div>
        <div className="progress mt-6">
          <span data-on="true" />
          <span data-on={steps >= 2 ? "true" : "false"} />
          <span data-on={steps >= 3 ? "true" : "false"} />
        </div>
      </div>

      <div className="card mt-14" style={{ display: "grid", gap: 12 }}>
        <div className="flex items-start gap-10">
          <Check size={16} className="green" style={{ marginTop: 3 }} />
          <div>
            <p className="card-title" style={{ fontSize: 15 }}>酒店已选好</p>
            <p className="caption" style={{ margin: "2px 0 0" }}>江景客栈 · 保留选择，尚未代订</p>
          </div>
        </div>
        <div className="flex items-start gap-10">
          {demo.project.transport ? (
            <Check size={16} className="green" style={{ marginTop: 3 }} />
          ) : (
            <span
              className="tabular"
              style={{ width: 18, height: 18, borderRadius: "50%", background: "var(--m-mist)", textAlign: "center", fontSize: 12, lineHeight: "18px", marginTop: 2 }}
            >
              2
            </span>
          )}
          <div>
            <p className="card-title" style={{ fontSize: 15 }}>
              {demo.project.transport ? "交通已确认" : "下一步：确认交通"}
            </p>
            <p className="caption" style={{ margin: "2px 0 0" }}>
              {demo.project.transport ? "你已手动标记完成" : "确认交通方式与出发时间"}
            </p>
          </div>
        </div>
        <div className="flex items-start gap-10" style={{ opacity: demo.project.transport ? 1 : 0.6 }}>
          {demo.project.packing ? (
            <Check size={16} className="green" style={{ marginTop: 3 }} />
          ) : (
            <span
              className="tabular"
              style={{ width: 18, height: 18, borderRadius: "50%", background: "var(--m-mist)", textAlign: "center", fontSize: 12, lineHeight: "18px", marginTop: 2 }}
            >
              3
            </span>
          )}
          <div>
            <p className="card-title" style={{ fontSize: 15 }}>
              {demo.project.transport && !demo.project.packing ? "下一步：整理行李" : "整理行李"}
            </p>
            <p className="caption" style={{ margin: "2px 0 0" }}>
              {demo.project.packing
                ? "你已手动标记完成"
                : demo.project.transport
                  ? "按已确认的行程整理行李"
                  : "等交通确认后，再按行程整理"}
            </p>
          </div>
        </div>
      </div>

      {steps < 3 ? (
        <button type="button" className="btn btn--primary mt-14" onClick={demo.advanceProject}>
          {demo.project.transport ? "我已整理好行李" : "我已确认交通"}
        </button>
      ) : (
        <div className="notice notice--green mt-14">准备完成，之前的决定都还在。</div>
      )}
      <p className="micro mt-8">只记录你的确认，不会替你订酒店或购买车票。</p>
      <Link href="/matters/evidence/project" className="link-button mt-4">
        <FileText size={14} /> 查看上次的决定和笔记
      </Link>
    </>
  );
}

function TravelBody({ matter }: { matter: DemoMatter }) {
  const demo = useDemo();
  const contacts = [
    "联系接站人，确认新的接站时间",
    "联系活动方，确认迟到或调整方案",
  ];

  return (
    <>
      <div className="card">
        <p className="micro">10月26日 · 行程连锁变化</p>
        <h2 className="serif mt-4" style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>
          一处改期，后面的安排也照看一下。
        </h2>
        <div className="flex items-center gap-10 mt-14" style={{ flexWrap: "wrap" }}>
          <div>
            <p className="tabular" style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>
              {demo.travel.imported ? "14:00" : "11:00"}
            </p>
            <span className="micro">预计到达</span>
          </div>
          <ArrowRight size={15} className="faint" />
          <div>
            <p className="tabular" style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>11:30</p>
            <span className="micro">原定接站</span>
          </div>
          <ArrowRight size={15} className="faint" />
          <div>
            <p className="tabular" style={{ margin: 0, fontSize: 24, fontWeight: 600 }}>13:00</p>
            <span className="micro">原定活动</span>
          </div>
        </div>
        <p className="caption mt-12" style={{ marginBottom: 0 }}>{matter.subtitle}</p>
      </div>

      {!demo.travel.imported ? (
        <>
          <p className="body-text mt-14">试着导入一条到达时间变化的通知，看看哪些后续安排需要你处理。</p>
          <button type="button" className="btn btn--primary mt-10" onClick={demo.importTravel}>
            演示：导入改期通知
          </button>
        </>
      ) : !demo.travel.accepted ? (
        <>
          <Notice tone="amber" title="发现 2 处冲突">
            新的 14:00 到达时间，晚于原定接站与活动开始时间。
          </Notice>
          <div className="card mt-10" style={{ display: "grid", gap: 8 }}>
            <div className="flex items-center justify-between gap-10">
              <span className="caption" style={{ margin: 0 }}>接站 11:30</span>
              <strong style={{ fontSize: 13 }}>建议：联系接站人确认新时间</strong>
            </div>
            <div className="flex items-center justify-between gap-10">
              <span className="caption" style={{ margin: 0 }}>活动 13:00</span>
              <strong style={{ fontSize: 13 }}>建议：联系活动方说明可能迟到</strong>
            </div>
          </div>
          <button type="button" className="btn btn--primary mt-14" onClick={demo.acceptTravel}>
            采纳调整草稿
          </button>
          <p className="micro mt-8">采纳后仅生成待联系事项。不会发送消息、取消安排或自动改签。</p>
        </>
      ) : (
        <>
          <div className="notice notice--green mt-14">已生成 2 条待联系事项</div>
          <div className="card mt-10" style={{ display: "grid", gap: 4 }}>
            {contacts.map((contact, index) => {
              const id = String(index);
              const done = demo.travel.contacts.includes(id);
              return (
                <label className="check" key={contact}>
                  <input
                    type="checkbox"
                    checked={done}
                    onChange={() => demo.toggleContact(id)}
                  />
                  <span>
                    <span style={{ display: "block", fontSize: 14 }}>{contact}</span>
                    <span className="caption" style={{ display: "block", fontSize: 12 }}>
                      {done ? "你已手动确认联系完成" : "待你联系 · 不会自动发送"}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          <p className="micro mt-8">原安排未被自动改写，实际调整以你与相关人员确认的结果为准。</p>
        </>
      )}

      <Link href="/matters/evidence/travel" className="link-button mt-6">
        <FileText size={14} /> 查看原行程与示例通知
      </Link>
    </>
  );
}

function GenericBody({ matter }: { matter: DemoMatter }) {
  const evidence = evidenceFor(matter);
  return (
    <>
      <div className="card">
        <div className="flex items-start justify-between gap-10">
          <div>
            <p className="micro">{MATTER_KIND_LABEL[matter.kind]} · {matter.dateText}</p>
            <h2 className="serif mt-4" style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
              {matter.title}
            </h2>
          </div>
          {matter.amount ? (
            <span className="tabular" style={{ fontSize: 26, fontWeight: 600 }}>
              ¥{matter.amount}
            </span>
          ) : null}
        </div>
        <p className="caption mt-4">{matter.subtitle}</p>
        <div className="mt-12" style={{ display: "grid", gap: 8 }}>
          {matter.detail.map((row) => (
            <div key={row.label} className="flex items-start justify-between gap-14">
              <span className="caption" style={{ margin: 0 }}>{row.label}</span>
              <span style={{ fontSize: 14, textAlign: "right", overflowWrap: "anywhere" }}>{row.value}</span>
            </div>
          ))}
        </div>
      </div>

      {matter.kind === "unknown" ? (
        <Notice tone="amber" title="没有替你补全">
          当前示例无法可靠判断它属于哪件事，所以没有自动设置日期、金额或待办。保留原文，等你确认。
        </Notice>
      ) : matter.kind === "order" ? (
        <Notice title="订单只保存在这里">
          后续物流或售后消息会归到同一件事；不会自动联系商家或改变订单状态。
        </Notice>
      ) : null}

      {evidence ? (
        <Link href={`/matters/evidence/${evidence}`} className="link-button mt-6">
          <FileText size={14} /> 查看原始依据
        </Link>
      ) : null}
    </>
  );
}

export function MatterDetail({ id }: { id: string }) {
  const demo = useDemo();
  const router = useRouter();
  const matter = demo.matters.find((item) => item.id === id) ?? null;

  if (!demo.enabled) {
    return (
      <PageShell title="事务详情">
        <EmptyState
          title="事务详情需要服务器接口"
          body="当前后端没有事务、退款或订单接口，正常模式不展示示例详情。你可以在“我的”里显式开启演示模式查看交互。"
        >
          <button type="button" className="btn btn--secondary" onClick={() => demo.setEnabled(true)}>
            开启演示模式
          </button>
        </EmptyState>
      </PageShell>
    );
  }

  if (!matter) {
    return (
      <PageShell title="事务详情">
        <EmptyState
          title="找不到这件事"
          body="它可能已被删除，或者只存在于另一个演示会话里。"
        >
          <button type="button" className="btn btn--secondary" onClick={() => router.push("/matters")}>
            返回事务列表
          </button>
        </EmptyState>
      </PageShell>
    );
  }

  return (
    <PageShell
      title={MATTER_KIND_LABEL[matter.kind]}
      subtitle={`${matter.title} · ${matter.dateText}`}
      action={<Pill tone={matter.status === "done" ? "green" : "fog"}>{matter.status === "done" ? "已完成" : "进行中"}</Pill>}
    >
      {matter.kind === "refund" ? <RefundBody matter={matter} /> : null}
      {matter.kind === "ticket" ? <TicketBody matter={matter} /> : null}
      {matter.kind === "wish" ? <WishBody matter={matter} /> : null}
      {matter.kind === "project" ? <ProjectBody /> : null}
      {matter.kind === "travel" ? <TravelBody matter={matter} /> : null}
      {matter.kind === "order" || matter.kind === "unknown" ? <GenericBody matter={matter} /> : null}

      <p className="micro mt-20" style={{ lineHeight: 1.65 }}>
        演示模式 · 这里的状态只保存在当前页面内，刷新后恢复初始示例；不会同步到服务器或外部服务。
      </p>
      <div className="mt-12">
        <LinkRow
          icon={<MapPin size={14} />}
          title="所有事务"
          detail="回到列表继续查看"
          onClick={() => router.push("/matters")}
        />
      </div>
    </PageShell>
  );
}
