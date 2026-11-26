import type { ReactNode } from "react";
import type { IntakePendingContext } from "@/lib/mobile/api";

/** Display confirmed writes even if a later operation is uncertain; allow editing only after a known non-write. */
export function IntakeFailureActions({ context, onEdit, onCheck, details }: {
  context?: IntakePendingContext | null;
  onEdit?: () => void;
  onCheck?: () => void;
  details?: ReactNode;
}) {
  const state = context?.write_state ?? "uncertain";
  const message = {
    applied: "已经替你存好了。",
    not_applied: "没能保存成功",
    uncertain: "暂时不确定有没有保存成功",
  }[state];
  return <>
    <p className="ask-failure__body">{state === "applied" && context?.applied_receipts?.length
      ? `已经替你存好了：${context.applied_receipts.map((receipt) => receipt.summary).join("；")}`
      : message}</p>
    {state === "uncertain" && context?.applied_receipts?.map((receipt, i) => <p key={i}>{receipt.summary}</p>)}
    <div className="ask-failure__actions">
      {state === "uncertain" && onCheck && <button type="button" className="btn btn--secondary btn--small" onClick={onCheck}>查一下结果</button>}
      {state !== "not_applied" && <a className="link-button" href="/today">{state === "applied" ? "去看看" : "去「今天」看看"}</a>}
      {state === "not_applied" && onEdit && <button type="button" className="btn btn--secondary btn--small" onClick={onEdit}>改改再发</button>}
      {details}
    </div>
  </>;
}
