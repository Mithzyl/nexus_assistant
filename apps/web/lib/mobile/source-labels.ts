/** User-facing labels for source upload state without exposing server codes. */

export function uploadFailureText(code: string | null | undefined): string {
  if (!code) return "";
  if (code === "upload_field_not_authorized") return "有字段未获上传授权";
  if (code === "scope_not_authorized") return "所选日历或提醒范围未获授权";
  if (code === "batch_content_mismatch") return "上传批次内容与声明不一致";
  if (code === "sync_disabled") return "来源已停止继续上传";
  return "最近一次上传失败，请打开来源查看并重试";
}

export function uploadStatusText(status: string | null | undefined): string {
  if (!status) return "";
  if (status === "ok" || status === "success" || status === "succeeded") return "成功";
  if (status === "failed" || status === "error") return "失败";
  return "状态待核对";
}
