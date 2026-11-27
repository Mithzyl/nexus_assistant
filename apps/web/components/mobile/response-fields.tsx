import type { ResponseField } from "@/lib/mobile/api";
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
export function Fields({ items }: { items: ResponseField[] }) {
  return <dl className={styles.fields}>{items.map((field, i) => <div key={i}><dt>{field.label}</dt><dd>{displayValue(field)}</dd></div>)}</dl>;
}
