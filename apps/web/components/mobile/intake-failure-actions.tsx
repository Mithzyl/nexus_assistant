import type { IntakePendingContext } from "@/lib/mobile/api";

/** Display confirmed writes even if a later operation is uncertain; allow editing only after a known non-write. */
export function IntakeFailureActions({ context, onEdit, onCheck }: {
  context?: IntakePendingContext | null;
  onEdit?: () => void;
  onCheck?: () => void;
}) {
  const state = context?.write_state ?? "uncertain";
  const message = {
    applied: "已保存业务修改，但助手回复未完成。",
    not_applied: "本次确认未写入任何安排。",
    uncertain: "写入结果尚待核对，请先核对已有安排，避免重复创建。",
  }[state];
  return <>
    <p className="ask-failure__body">{message}</p>
    {context?.applied_receipts?.map((receipt, i) => <p key={i}>已保存 · {receipt.summary}</p>)}
    <div className="ask-failure__actions">
      {state !== "not_applied" && <a href="/today">去「今天」页核对</a>}
      {state === "uncertain" && onCheck && <button type="button" onClick={onCheck}>查询原结果</button>}
      {state === "not_applied" && onEdit && <button type="button" onClick={onEdit}>重新编辑</button>}
    </div>
  </>;
}
