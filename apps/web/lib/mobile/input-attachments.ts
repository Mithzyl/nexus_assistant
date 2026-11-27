import { apiRequest, pa } from "./api";
import { IMAGE_UPLOAD_LIMIT_BYTES, uploadImage } from "./image-attachments";

export type AttachmentKind = "image" | "markdown";
export interface AttachmentReference {
  kind: AttachmentKind;
  attachment_id: string;
}
export interface InputAttachment extends AttachmentReference {
  filename?: string;
  title?: string;
  completed_count?: number;
  unfinished_count?: number;
}

export const INPUT_ATTACHMENT_LIMIT = 4;
export const INPUT_FILE_ACCEPT = "image/png,image/jpeg,image/webp,.md,.markdown";
const MARKDOWN_LIMIT_BYTES = 200 * 1024;

/** Find the matching upload type and give a clear error before sending large files. */
export function validateInputAttachment(file: File): AttachmentKind {
  if (/\.(md|markdown)$/i.test(file.name)) {
    if (file.size > MARKDOWN_LIMIT_BYTES) throw new Error("这个文件太大了，请缩短后再上传。");
    return "markdown";
  }
  if (["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
    (!file.type && /\.(jpeg|jpg|png|webp)$/i.test(file.name))) {
    if (file.size > IMAGE_UPLOAD_LIMIT_BYTES) throw new Error("这张图片太大了，请缩小后再上传。");
    return "image";
  }
  throw new Error("这个文件暂时打不开，请换一张图片或选择 .md 文件。");
}

/** Upload one private file using the same authenticated browser proxy as other user data. */
export async function uploadInputAttachment(file: File, kind: AttachmentKind, matterId?: string): Promise<InputAttachment> {
  if (kind === "image") {
    const uploaded = await uploadImage(file, matterId);
    return { kind, attachment_id: uploaded.attachment_id };
  }
  return apiRequest<InputAttachment>(pa("/markdown-attachments"), {
    method: "POST", rawBody: file, headers: {
      "Content-Type": "application/octet-stream",
      "X-Attachment-Filename": encodeURIComponent(file.name),
      ...(matterId ? { "X-Matter-Id": matterId } : {}),
    },
  });
}

/** Strip display names and counts before submitting the server references. */
export function attachmentReferences(files: InputAttachment[]): AttachmentReference[] {
  return files.map(({ kind, attachment_id }) => ({ kind, attachment_id }));
}

/** An existing request key can only be reused with the same ordered attachments. */
export function sameAttachmentReferences(first: AttachmentReference[] = [], second: AttachmentReference[] = []): boolean {
  return first.length === second.length && first.every((entry, index) =>
    entry.attachment_id === second[index].attachment_id && entry.kind === second[index].kind);
}

/** Read a Markdown original as inert text, using the user's authenticated session. */
export function readMarkdownAttachment(id: string, signal?: AbortSignal) {
  return apiRequest<InputAttachment & { text: string }>(
    pa("/markdown-attachments/" + encodeURIComponent(id)), { signal });
}

/** List private documents linked to the selected matter. */
export function listMatterMarkdown(matterId: string, signal?: AbortSignal) {
  return apiRequest<InputAttachment[]>(pa("/markdown-attachments?" + new URLSearchParams({ matter_id: matterId })), { signal });
}
