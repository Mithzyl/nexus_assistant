"use client";

import { useId, useMemo, useState } from "react";
import { Footprints, MapPin } from "lucide-react";
import { isLegacyRouteItinerary, RouteTimeline } from "./route-timeline";
import { AmapMapCanvas, type AmapPoint } from "./amap-map";
import type { EmbeddedCard, MapRouteGroup, ResponseBlock, ResponseField, ResponseImage, IntakePendingContext } from "@/lib/mobile/api";
import { Fields } from "./response-fields";
import { formatDateTime } from "@/lib/mobile/format";
import styles from "./response-card.module.css";

/** Show an image with an accessible description and a useful unavailable state. */
function CardImage({ value, thumbnail = false }: { value: ResponseImage; thumbnail?: boolean }) {
  const [failed, setFailed] = useState(false);
  return <figure className={thumbnail ? styles.thumbnail : styles.figure}>
    {failed ? <div className={styles.imageFallback} role="img" aria-label={value.alt}>图片没显示出来</div> :
      // Remote image sources are validated by Assistant; no provider credentials are sent.
      // eslint-disable-next-line @next/next/no-img-element
      <img src={value.src} alt={value.alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />}
    {value.caption && !thumbnail ? <figcaption>{value.caption}</figcaption> : null}
  </figure>;
}

/** Render only the declared presentation primitives; cards cannot introduce executable UI. */
function Block({ block, route = false, itinerary = false }: { block: ResponseBlock; route?: boolean; itinerary?: boolean }) {
  switch (block.type) {
    case "text": return <p className={styles.text}>{block.text}</p>;
    case "fields": return <Fields items={block.items} />;
    case "image": return <CardImage value={block} />;
    case "timeline": return itinerary ? <RouteTimeline items={block.items} /> : <ol className={route ? styles.routeTimeline : styles.timeline}>{block.items.map((item, i) => <li key={i}>
      {item.connector && i > 0 ? <p className={styles.connector}>{item.connector}</p> : null}
      <div className={styles.stop}>
        <div className={styles.stopHeading}>
          {item.image ? <CardImage value={item.image} thumbnail /> : route ? (
            <span className={styles.routeIcon} aria-hidden="true">{item.title.startsWith("步行") ? <Footprints size={18} /> : <MapPin size={18} />}</span>
          ) : null}
          <div className={styles.stopTitle}>
            {item.category ? <span className={styles.category}>{item.category}</span> : null}
            <h4>{route ? null : <span className={styles.number}>{i + 1}</span>}{item.title}</h4>
            {item.subtitle ? <p className={styles.muted}>{item.subtitle}</p> : null}
          </div>
        </div>
        {item.time ? <p className={styles.time}>{item.time}</p> : null}
        {item.description ? <p className={route ? styles.routeDescription : styles.muted}>{item.description}</p> : null}
        {item.fields?.length ? <Fields items={item.fields} /> : null}
        {item.link ? <a className={styles.link} href={item.link.href} target="_blank" rel="noopener noreferrer">{item.link.label} ↗</a> : null}
      </div>
    </li>)}</ol>;
    default: return null;
  }
}

/** Offer a map for provider geometry while keeping the time line independently readable. */
function TimelineMap({ payload }: { payload: EmbeddedCard["payload"] }) {
  const [open, setOpen] = useState(false);
  const segments = (payload?.shape?.segments ?? []).filter(
    (segment) => segment.coordinate_system === "gcj02" && segment.points.length >= 2,
  );
  const points: AmapPoint[] = [];
  if (payload?.origin) points.push({ ...payload.origin, id: "origin", label: "出发点", coordinateSystem: payload.origin.coordinate_system, kind: "origin" });
  if (payload?.destination) points.push({ ...payload.destination, id: "destination", label: "目的地", coordinateSystem: payload.destination.coordinate_system, kind: "destination" });
  const center = segments[0]?.points[0];
  if (!center) return null;
  return <div>
    <button type="button" aria-expanded={open} className={styles.link} onClick={() => setOpen(!open)}>
      {open ? "收起路线地图" : "查看路线地图"}
    </button>
    {open ? <AmapMapCanvas purpose="route" points={points} focusCenter={[center.longitude, center.latitude]}
      routeSegments={segments.map((segment) => ({ ...segment, coordinateSystem: segment.coordinate_system }))} /> : null}
  </div>;
}

const GROUP_COLORS = ["#315e72", "#8e4c32", "#3d6a49", "#725488", "#8a6525", "#316c66"];

/** Switch the map geometry, labels and text itinerary together. */
function MultiRouteMap({ groups }: { groups: MapRouteGroup[] }) {
  const regionId = useId();
  const [selectedId, setSelectedId] = useState(groups[0]?.group_id);
  const position = Math.max(0, groups.findIndex((group) => group.group_id === selectedId));
  const selected = groups[position];
  const { points, segments } = useMemo(() => {
    const points: AmapPoint[] = (selected?.markers ?? []).flatMap((marker, index, markers) =>
      marker.coordinate_system === "gcj02" ? [{
        ...marker, id: String(index), coordinateSystem: marker.coordinate_system,
        kind: selected?.shape?.segments?.length ? (index === 0 ? "origin" : index === markers.length - 1 ? "destination" : "saved") : "saved",
      }] : [],
    );
    const segments = (selected?.shape?.segments ?? []).filter(
      (segment) => segment.coordinate_system === "gcj02" && segment.points.length >= 2,
    ).map((segment) => ({ ...segment, coordinateSystem: segment.coordinate_system }));
    return { points, segments };
  }, [selected]);
  if (!selected) return null;
  const center = segments[0]?.points[0] ?? points[0];
  return <div className={styles.blocks}>
    <div className={styles.routeGroups} role="group" aria-label="选择路线">
      {groups.map((group, index) => (
        <button key={group.group_id} type="button" className={styles.routeGroup}
          style={{ color: GROUP_COLORS[index] }} aria-controls={regionId}
          aria-pressed={selected.group_id === group.group_id} onClick={() => setSelectedId(group.group_id)}>
          <span className={styles.routeSwatch} aria-hidden="true" />{group.label}
          {group.distance_meters != null ? (
            <span>{new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(group.distance_meters / 1000)} 公里</span>
          ) : null}
        </button>
      ))}
    </div>
    <section id={regionId} aria-label={selected.label} className={styles.blocks}>
      {center ? <AmapMapCanvas purpose="route" passive points={points} routeSegments={segments}
        routeColor={GROUP_COLORS[position]} focusCenter={[center.longitude, center.latitude]} /> : null}
      {points.length > 0 && !segments.length ? <p className={styles.muted}>地图仅标出已知地点。</p> : null}
      {selected.blocks.map((block, index) => <Block key={index} block={block} route />)}
    </section>
  </div>;
}

/** Render the owned matter summary, with a single optional time line and an adjustment action. */
function MatterProgressCard({ card, onAdjust }: { card: EmbeddedCard; onAdjust?: () => void }) {
  const groups = [
    ["已完成", card.payload?.completed_items],
    ["正在关注", card.payload?.watching_items],
    ["新变化", card.payload?.latest_changes],
    ["等你拍板", card.payload?.next_decisions],
  ] as const;
  const embedded = card.payload?.embedded_card;
  return <article className={styles.card} aria-label={card.title}>
    <header className={styles.header}>
      <h3>{card.title}</h3>
      {onAdjust ? <button type="button" className={styles.link} onClick={onAdjust}>让助手调整</button> : null}
    </header>
    {card.updated_at ? <p className={styles.muted}>更新于 {formatDateTime(card.updated_at)}</p> : null}
    <div className={styles.blocks}>
      {groups.map(([label, items]) => items?.length ? <section key={label} className={styles.progressGroup}>
        <h4>{label}</h4><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
      </section> : null)}
      {embedded && (embedded.card_type === "timeline_route" || embedded.card_type === "multi_route_map") ? <div className={styles.embedded}>
        <h4>{embedded.title}</h4>
        {embedded.payload?.groups?.length ? <MultiRouteMap groups={embedded.payload.groups} /> : <>
          {embedded.blocks.map((block, i) => <Block key={i} block={block} itinerary />)}
          <TimelineMap payload={embedded.payload} />
        </>}
      </div> : null}
    </div>
  </article>;
}

/** Render a model-composed result, plan or information card with server-derived status. */
export function EmbeddedResponseCard({ card, onAdjust }: { card: EmbeddedCard; onAdjust?: () => void }) {
  if (card.card_type === "long_task_progress" && card.payload) return <MatterProgressCard card={card} onAdjust={onAdjust} />;
  const route = card.card_type === "timeline_route";
  const itinerary = route || isLegacyRouteItinerary(card);
  const mapGroups = card.card_type === "multi_route_map" ? card.payload?.groups : undefined;
  const status = { completed: "已完成", failed: "未完成", unknown: "待核对", suggestion: "建议安排", info: "参考信息" }[card.status];
  const titleIncludesStatus = card.status === "completed" && /已完成|已保存|已创建|已更新|已取消|已删除/.test(card.title)
    || card.status === "failed" && /失败|未完成|没能/.test(card.title)
    || Boolean(status && card.title.includes(status));
  return <article className={itinerary ? styles.itineraryCard : styles.card} aria-label={card.title} data-status={card.status}>
    <header className={styles.header}>
      <h3>{card.title}</h3>
      {!itinerary && !mapGroups?.length && !titleIncludesStatus && (card.kind !== "result" || card.status !== "completed") ? <span className={styles.badge}>{status}</span> : null}
    </header>
    {card.subtitle ? <p className={itinerary ? styles.itinerarySubtitle : styles.muted}>{card.subtitle}</p> : null}
    {mapGroups?.length ? <MultiRouteMap groups={mapGroups} /> : (
      <div className={styles.blocks}>{card.blocks.map((block, i) => <Block key={i} block={block} itinerary={itinerary} />)}</div>
    )}
    {route ? <TimelineMap payload={card.payload} /> : null}
    {route && card.payload?.detail_href ? <a className={styles.link} href={card.payload.detail_href}>查看事项</a> : null}
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

/** Honor explicit card lists, including no cards; only older responses fall back to their retained receipt. */
export function ApiOperationCard({ context }: { context: IntakePendingContext }) {
  if (Array.isArray(context.cards)) {
    if (context.cards.length === 0) return null;
    return <section className={styles.stack} aria-label="助手卡片">{context.cards.map((card, i) => <EmbeddedResponseCard key={i} card={card} />)}</section>;
  }
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
