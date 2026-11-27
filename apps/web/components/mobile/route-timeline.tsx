"use client";

import { useState } from "react";
import { BedDouble, Briefcase, BusFront, CarFront, ClipboardList, Footprints, MapPin, MoveDown, Utensils } from "lucide-react";
import type { EmbeddedCard, ResponseTimelineItem } from "@/lib/mobile/api";
import { Fields } from "./response-fields";
import styles from "./response-card.module.css";

const LEADING_DATE = /^(?:\d{4}[-/.年]\s*)?\d{1,2}(?:[-/.]\d{1,2}|月\s*\d{1,2}日)(?:\s*[（(]?(?:周|星期)[一二三四五六日天][）)]?)?(?=$|\s|[·｜|，,：:—–])/u;
const STAY_FIELD = /^(?:住宿(?:地点)?|留宿|过夜|落脚(?:地点)?|酒店)[：:]?$/u;
const STAY_CLAUSE = /(^|[。；;，,\n])\s*(?:今晚)?(?:夜宿|留宿|住宿\s*[:：]|落脚\s*[:：]|宿(?!舍|醉|营))\s*[:：]?\s*([^。；;，,\n！？!?]{1,40})(?=$|[。；;，,\n！？!?])/gu;
const TRAVEL_CATEGORY = /出发|返程|转场|游玩|景点|餐饮|交通|住宿/u;

/** Split a date badge from existing text without guessing dates or discarding a time of day. */
function splitDate(text?: string) {
  const value = text?.trim() ?? "";
  const date = value.match(LEADING_DATE)?.[0];
  return { date, text: date ? value.slice(date.length).replace(/^[\s·｜|，,：:—–]+/u, "") : value };
}

/** Recognize saved itinerary cards from their dated travel nodes and leave other old timelines alone. */
export function isLegacyRouteItinerary(card: EmbeddedCard) {
  if (card.card_type || card.kind === "result") return false;
  return card.blocks.some((block) => {
    if (block.type !== "timeline" || !block.items.some((item) => TRAVEL_CATEGORY.test(item.category ?? ""))) return false;
    const dates = block.items.map((item) => splitDate(item.time).date ?? splitDate(item.title).date);
    return new Set(dates.filter(Boolean)).size >= 2;
  });
}

/** Move explicitly labelled overnight stops out of prose while preserving the surrounding itinerary. */
function splitStay(text: string, stays: Set<string>) {
  return text.replace(STAY_CLAUSE, (_, _separator: string, place: string) => {
    stays.add(place.trim());
    return "";
  }).replace(/^[\s。；;，,]+/u, "").trim();
}

/** Pick the visual anchor for an existing category or an explicitly named travel mode. */
function categoryStyle(value: string) {
  if (/步行|徒步/u.test(value)) return { tone: "visit", Icon: Footprints };
  if (/公交|巴士|轨道|火车|高铁|地铁/u.test(value)) return { tone: "travel", Icon: BusFront };
  if (/出发|转场|返程|车程|交通|乘|驾车/u.test(value)) return { tone: "travel", Icon: CarFront };
  if (/游玩|景点|浏览|参观/u.test(value)) return { tone: "visit", Icon: MapPin };
  if (/餐饮|吃饭|美食|午餐|晚餐/u.test(value)) return { tone: "food", Icon: Utensils };
  if (/工作|会议|办事/u.test(value)) return { tone: "work", Icon: Briefcase };
  if (/住宿|酒店|落脚/u.test(value)) return { tone: "stay", Icon: BedDouble };
  return { tone: "general", Icon: ClipboardList };
}

/** Show thumbnails only from Assistant's protected image store, with a category icon on failure. */
function RouteNodeMedia({ item, display }: { item: ResponseTimelineItem; display: ReturnType<typeof categoryStyle> }) {
  const path = item.image?.src.match(/^(?:\/api\/(?:pa|v1))?(\/image-attachments\/[A-Za-z0-9_-]{1,36})\/(?:original|thumbnail)$/u)?.[1];
  const src = path ? `/api/pa${path}/thumbnail` : undefined;
  const [failedSrc, setFailedSrc] = useState<string>();
  const { Icon, tone } = display;
  return <span className={styles.itineraryMedia} data-tone={tone}>
    {src && src !== failedSrc
      // eslint-disable-next-line @next/next/no-img-element
      ? <img src={src} alt={item.image?.alt ?? ""} loading="lazy" onError={() => setFailedSrc(src)} />
      : <Icon size={23} aria-hidden="true" />}
  </span>;
}

/** Display a prose transition outside both adjacent node cards. */
function RouteTransition({ text }: { text?: string }) {
  const { Icon, tone } = categoryStyle(text ?? "");
  const TransitionIcon = tone === "general" ? MoveDown : Icon;
  return text ? <div className={styles.itineraryTransition} data-route-transition>
    <span className={styles.itineraryTransitionIcon} aria-hidden="true"><TransitionIcon size={15} /></span>
    <span>{text}</span>
  </div> : <div className={styles.itineraryConnector} aria-hidden="true" />;
}

/** Read a saved route node's existing fields and display travel cues in their own rows. */
function RouteNode({ item }: { item: ResponseTimelineItem }) {
  const title = splitDate(item.title);
  const subtitle = splitDate(item.subtitle);
  const time = splitDate(item.time);
  const date = time.date ?? title.date ?? subtitle.date;
  const heading = title.text || item.title;
  const categories = [...new Set((item.category ?? "").split(/[+＋/／、｜|]/u).map((value) => value.trim()).filter(Boolean))];
  const stays = new Set<string>();
  const details = [splitStay(subtitle.text, stays), splitStay(item.description ?? "", stays)].filter(Boolean);
  const otherFields = item.fields?.filter((field) => {
    if (!STAY_FIELD.test(field.label.trim()) || typeof field.value !== "string" || !field.value.trim()) return true;
    stays.add(field.value.trim());
    return false;
  });
  return <div className={styles.itineraryNode} data-route-node>
    <RouteNodeMedia item={item} display={categoryStyle(categories[0] || heading)} />
    <div className={styles.itineraryNodeBody}>
      {date || categories.length > 0 ? <div className={styles.itineraryBadges}>
        {date ? <span className={styles.itineraryDate}
          data-weekend={/(?:周|星期)[六日天]/u.test(date)}>{date}</span> : null}
        {categories.map((category) => <span key={category} className={styles.itineraryCategory}
          data-tone={categoryStyle(category).tone}>{category}</span>)}
      </div> : null}
      <h4>{heading}</h4>
      {time.text ? <p className={styles.itineraryTime}>{time.text}</p> : null}
      {details.length > 0 ? <p className={styles.itineraryDescription}>{details.join(" ")}</p> : null}
      {stays.size > 0 ? <div className={styles.itineraryStays}>
        {[...stays].filter(Boolean).map((place) => <span key={place} className={styles.itineraryStay} data-tone="stay">
          <BedDouble size={14} aria-hidden="true" />住 {place}
        </span>)}
      </div> : null}
      {otherFields?.length ? <Fields items={otherFields} /> : null}
      {item.link ? <a className={styles.link} href={item.link.href} target="_blank" rel="noopener noreferrer">{item.link.label} ↗</a> : null}
    </div>
  </div>;
}

/** Present route nodes and unboxed transitions in their saved order. */
export function RouteTimeline({ items }: { items: ResponseTimelineItem[] }) {
  return <ol className={styles.itineraryTimeline} aria-label="分段行程">{items.map((item, index) => <li key={index}>
    {index > 0 ? <RouteTransition text={item.connector} /> : null}
    <RouteNode item={item} />
  </li>)}</ol>;
}
