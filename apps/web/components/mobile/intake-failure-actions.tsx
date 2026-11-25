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
    applied: "日程或待办已保存，但助手回复失败。",
    not_applied: "这次没有保存任何更改。",
    uncertain: "还没确认是否保存成功，先去「今天」页看看。",
  }[state];
  return <>
    <p className="ask-failure__body">{message}</p>
    {state !== "not_applied" && context?.applied_receipts?.map((receipt, i) => <p key={i}>{receipt.summary}</p>)}
    <div className="ask-failure__actions">
      {state !== "not_applied" && <a className="btn btn--secondary btn--small" href="/today">去「今天」页核对</a>}
      {state === "uncertain" && onCheck && <button type="button" className="link-button" onClick={onCheck}>查询原结果</button>}
      {state === "not_applied" && onEdit && <button type="button" className="btn btn--secondary btn--small" onClick={onEdit}>重新编辑</button>}
      {details}
    </div>
  </>;
}
