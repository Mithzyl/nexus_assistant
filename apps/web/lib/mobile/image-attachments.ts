import { apiRequest, pa } from "./api";

export interface ImageAttachment {
  attachment_id: string;
  matter_id: string | null;
  media_type: string;
  width: number;
  height: number;
  byte_size: number;
  caption: string;
  created_at: string;
  thumbnail_path: string;
  original_path: string;
}

export const IMAGE_UPLOAD_LIMIT_BYTES = 8 * 1024 * 1024;

/** Fetch card metadata for the signed-in account or an owned matter. */
export function listImages(matterId?: string, signal?: AbortSignal) {
  const search = matterId ? "?" + new URLSearchParams({ matter_id: matterId }) : "";
  return apiRequest<ImageAttachment[]>(pa("/image-attachments" + search), { signal });
}

/** Send the original bytes and associate them with the chosen matter in one request. */
export function uploadImage(file: File, matterId?: string) {
  return apiRequest<ImageAttachment>(pa("/image-attachments"), {
    method: "POST", rawBody: file,
    headers: { "Content-Type": "application/octet-stream", ...(matterId ? { "X-Matter-Id": matterId } : {}) },
  });
}

/** Apply only changes explicitly supplied by the image owner. */
export function updateImage(attachmentId: string, body: { caption?: string; matter_id?: string | null }) {
  return apiRequest<ImageAttachment>(pa("/image-attachments/" + encodeURIComponent(attachmentId)), {
    method: "PATCH", body,
  });
}

/** Delete the selected saved photo and its preview together. */
export function deleteImage(attachmentId: string) {
  return apiRequest<void>(pa("/image-attachments/" + encodeURIComponent(attachmentId)), { method: "DELETE" });
}
