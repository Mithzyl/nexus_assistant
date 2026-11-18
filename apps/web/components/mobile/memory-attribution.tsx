import { formatDateTime } from "@/lib/mobile/format";

export interface MemorySource {
  text_origin?: string;
  source_quote?: string | null;
  original_source?: MemorySource | null;
}

/** Explain authorship from stored metadata without relabelling model wording as the user's own quote. */
export function MemoryAttribution({ source, text, recordedAt }: {
  source?: MemorySource;
  text?: string;
  recordedAt?: string;
}) {
  const label = source?.text_origin === "user_quote" ? "你的原话"
    : source?.text_origin === "model_rewrite" ? "助手整理，依据你的原话"
    : source?.text_origin === "retained_content" ? "沿用已有内容"
    : source?.text_origin === "legacy_preserved" ? "保留的历史内容"
    : "未记录内容作者";
  const originalQuote = source?.original_source?.source_quote;
  return <div className="m-stack-6">
    <p className="caption">{label}{recordedAt ? ` · ${formatDateTime(recordedAt)}` : ""}</p>
    {source?.source_quote && source.source_quote !== text ? <p className="caption" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>本次原话：{source.source_quote}</p> : null}
    {originalQuote && originalQuote !== source?.source_quote && originalQuote !== text ? <p className="caption" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>最初原话：{originalQuote}</p> : null}
  </div>;
}
