"use client";

import { useState } from "react";
import type { EmbeddedCard, ResponseBlock, ResponseField, ResponseImage, IntakePendingContext } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import styles from "./response-card.module.css";

/** Format a scalar according to the standard display hint rather than a business field name. */
function displayValue(field: ResponseField): string {
  if (field.value === null) return "未设置";
  if (typeof field.value === "boolean") return field.value ? "是" : "否";
  const value = String(field.value);
  if (field.format === "datetime") return formatDateTime(value);
  if (field.format === "number" && typeof field.value === "number") return new Intl.NumberFormat("zh-CN").format(field.value);
  return value;
}

/** Render generic labels and values in their model-provided order. */
function Fields({ items }: { items: ResponseField[] }) {
  return <dl className={styles.fields}>{items.map((field, i) => <div key={i}><dt>{field.label}</dt><dd>{displayValue(field)}</dd></div>)}</dl>;
}

/** Show an image with an accessible description and a useful unavailable state. */
function CardImage({ value, thumbnail = false }: { value: ResponseImage; thumbnail?: boolean }) {
  const [failed, setFailed] = useState(false);
  return <figure className={thumbnail ? styles.thumbnail : styles.figure}>
    {failed ? <div className={styles.imageFallback} role="img" aria-label={value.alt}>图片暂时无法显示</div> :
      // Remote image sources are validated by Assistant; no provider credentials are sent.
      // eslint-disable-next-line @next/next/no-img-element
      <img src={value.src} alt={value.alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />}
    {value.caption && !thumbnail ? <figcaption>{value.caption}</figcaption> : null}
  </figure>;
}

/** Render only the declared presentation primitives; cards cannot introduce executable UI. */
function Block({ block }: { block: ResponseBlock }) {
  switch (block.type) {
    case "text": return <p className={styles.text}>{block.text}</p>;
    case "fields": return <Fields items={block.items} />;
    case "image": return <CardImage value={block} />;
    case "timeline": return <ol className={styles.timeline}>{block.items.map((item, i) => <li key={i}>
      {item.connector && i > 0 ? <p className={styles.connector}>{item.connector}</p> : null}
      <div className={styles.stop}>
        <div className={styles.stopHeading}>
          {item.image ? <CardImage value={item.image} thumbnail /> : null}
          <div className={styles.stopTitle}>
            {item.category ? <span className={styles.category}>{item.category}</span> : null}
            <h4><span className={styles.number}>{i + 1}</span>{item.title}</h4>
            {item.subtitle ? <p className={styles.muted}>{item.subtitle}</p> : null}
          </div>
        </div>
        {item.time ? <p className={styles.time}>{item.time}</p> : null}
        {item.description ? <p className={styles.muted}>{item.description}</p> : null}
        {item.fields?.length ? <Fields items={item.fields} /> : null}
        {item.link ? <a className={styles.link} href={item.link.href} target="_blank" rel="noopener noreferrer">{item.link.label} ↗</a> : null}
      </div>
    </li>)}</ol>;
    default: return null;
  }
}

/** Render a model-composed result, plan or information card with server-derived status. */
export function EmbeddedResponseCard({ card }: { card: EmbeddedCard }) {
  const status = { completed: "已完成", failed: "未完成", unknown: "待核对", suggestion: "建议安排", info: "参考信息" }[card.status];
  return <article className={styles.card} aria-label={card.title} data-status={card.status}>
    <header className={styles.header}>
      <h3>{card.title}</h3>
      {card.kind !== "result" || card.status !== "completed" ? <span className={styles.badge}>{status}</span> : null}
    </header>
    {card.subtitle ? <p className={styles.muted}>{card.subtitle}</p> : null}
    <div className={styles.blocks}>{card.blocks.map((block, i) => <Block key={i} block={block} />)}</div>
  </article>;
}

const LEGACY_LABELS: Record<string, string> = { title: "名称", name: "名称", location: "地点", starts_at: "开始时间", ends_at: "结束时间", due_at: "截止时间", message: "说明" };

/** Keep older saved receipts readable; new responses provide their own labels and bindings. */
function legacyFields(value: unknown, depth = 0): ResponseField[] {
  if (!value || typeof value !== "object" || depth > 3) return [];
  if (Array.isArray(value)) return value.slice(0, 5).flatMap((item) => legacyFields(item, depth + 1));
  return Object.entries(value).flatMap(([key, entry]) => {
    if (["payload", "result_json", "context_json"].includes(key)) return [];
    if (LEGACY_LABELS[key] && (typeof entry === "string" || typeof entry === "number")) return [{ label: LEGACY_LABELS[key], value: entry, format: key.endsWith("_at") ? "datetime" as const : "text" as const }];
    return entry && typeof entry === "object" ? legacyFields(entry, depth + 1) : [];
  }).slice(0, 14);
}

/** Display declarative cards, falling back to old receipts without any confirmation button. */
export function ApiOperationCard({ context }: { context: IntakePendingContext }) {
  if (context.cards?.length) return <section className={styles.stack} aria-label="助手卡片">{context.cards.map((card, i) => <EmbeddedResponseCard key={i} card={card} />)}</section>;
  const receipt = context.execution_result;
  if (!receipt) return null;
  const data = receipt.data;
  const schedule = data && typeof data === "object" && "schedule" in data ? data.schedule : null;
  const fields = legacyFields(schedule ?? data);
  const title = receipt.status === "succeeded" ? "本次结果" : receipt.status === "failed" ? "这次没能完成" : "结果还在核实";
  return <section className={styles.stack} aria-label="操作结果"><article className={styles.card}>
    <header className={styles.header}><h3>{title}</h3></header>
    {fields.length ? <Fields items={fields} /> : null}
    {receipt.status === "failed" ? <p className={styles.muted}>{receipt.http_status === 409 ? "记录有变化，刷新后再试一下。" : "这次没有处理成功，可以换个说法告诉我。"}</p> : null}
  </article></section>;
}
