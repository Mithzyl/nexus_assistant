"use client";

import { FilePlus, Camera, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ClipboardEvent, DragEvent } from "react";

import { pa } from "@/lib/mobile/api";
import {
  attachmentReferences, INPUT_ATTACHMENT_LIMIT, INPUT_FILE_ACCEPT, readMarkdownAttachment,
  uploadInputAttachment, validateInputAttachment,
  type AttachmentKind, type AttachmentReference, type InputAttachment,
} from "@/lib/mobile/input-attachments";
import { readSession, writeSession } from "@/lib/mobile/storage";
import styles from "./input-attachments.module.css";

interface ComposerAttachment {
  key: string;
  name: string;
  kind: AttachmentKind;
  file?: File;
  uploaded?: InputAttachment;
}

/** Keep selected bytes in the composer and persist successful upload IDs for safe resubmission. */
export function useInputAttachments(storageKey: string | null, matterId?: string) {
  const [items, setItems] = useState<ComposerAttachment[]>([]);
  const [error, setError] = useState("");
  const selected = useRef<ComposerAttachment[]>([]);
  const activeStorage = useRef(storageKey);
  const accountStorageIsCurrent = activeStorage.current === storageKey;

  useEffect(() => {
    activeStorage.current = storageKey;
    const uploaded = storageKey ? readSession<InputAttachment[]>(storageKey, []) : [];
    selected.current = uploaded.map((item) => ({
      key: item.attachment_id, name: item.filename ?? "图片", kind: item.kind, uploaded: item,
    }));
    setItems(selected.current);
    setError("");
  }, [storageKey]);

  /** Keep the active list and stored references in step after each upload or edit. */
  const save = useCallback((next: ComposerAttachment[]) => {
    selected.current = next;
    setItems(next);
    if (storageKey) writeSession(storageKey, next.flatMap((item) => item.uploaded ? [item.uploaded] : []));
  }, [storageKey]);

  /** Add only files that fit the composer and keep a usable selection on partial failure. */
  function add(files: FileList | File[]) {
    if (!storageKey) return;
    setError("");
    const next = [...selected.current];
    for (const file of Array.from(files)) {
      if (next.length >= INPUT_ATTACHMENT_LIMIT) {
        setError("一次最多发四个附件，可以分开发送。");
        break;
      }
      try {
        const kind = validateInputAttachment(file);
        next.push({ key: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${next.length}`, name: file.name, kind, file });
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "这个文件没能加上。");
      }
    }
    save(next);
  }

  /** Upload only the files that still lack an ID, preserving IDs after a later error. */
  async function upload(): Promise<AttachmentReference[]> {
    const next = [...selected.current];
    const uploaded: InputAttachment[] = [];
    for (const [index, item] of next.entries()) {
      const attachment = item.uploaded ?? await uploadInputAttachment(item.file as File, item.kind, matterId);
      if (activeStorage.current !== storageKey) throw new Error("登录状态变了，请重新选择附件。");
      next[index] = { ...item, uploaded: attachment };
      uploaded.push(attachment);
      save([...next]);
    }
    return attachmentReferences(uploaded);
  }

  /** Clear exactly the accepted files, leaving any replacement selection available. */
  const clear = useCallback((accepted: AttachmentReference[]) => {
    if (!accepted.length) return;
    save(selected.current.filter((item) => !accepted.some((ref) => ref.attachment_id === item.uploaded?.attachment_id)));
    setError("");
  }, [save]);

  return {
    items: accountStorageIsCurrent ? items : [], error, add, upload, clear,
    remove: (key: string) => { save(selected.current.filter((item) => item.key !== key)); setError(""); },
  };
}

export type ComposerAttachments = ReturnType<typeof useInputAttachments>;

/** Provide paste and drop handlers that leave normal text and link drops alone. */
export function attachmentDropHandlers(attachments: ComposerAttachments, disabled: boolean) {
  return {
    onPaste: (event: ClipboardEvent) => {
      const files = event.clipboardData.files;
      if (disabled || !files.length) return;
      event.preventDefault();
      attachments.add(files);
    },
    onDragOver: (event: DragEvent) => {
      if (event.dataTransfer.types.includes("Files")) event.preventDefault();
    },
    onDrop: (event: DragEvent) => {
      const files = event.dataTransfer.files;
      if (!files.length) return;
      event.preventDefault();
      if (!disabled) attachments.add(files);
    },
  };
}

/** Offer the same file picker and phone camera controls in all three composers. */
export function AttachmentPicker({ attachments, disabled }: { attachments: ComposerAttachments; disabled: boolean }) {
  const choose = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  return <div className={styles.picker}>
    <button className="icon-button" type="button" disabled={disabled}
      onClick={() => choose.current?.click()} aria-label="添加图片或文件"><FilePlus size={19} /></button>
    <button className={`icon-button ${styles.camera}`} type="button" disabled={disabled}
      onClick={() => camera.current?.click()} aria-label="拍照"><Camera size={19} /></button>
    <input ref={choose} hidden type="file" accept={INPUT_FILE_ACCEPT} multiple aria-label="选择图片或文件"
      onChange={(event) => {
        if (event.target.files) attachments.add(event.target.files);
        event.target.value = "";
      }} />
    <input ref={camera} hidden type="file" accept="image/*" capture="environment" aria-label="拍照上传"
      onChange={(event) => {
        if (event.target.files) attachments.add(event.target.files);
        event.target.value = "";
      }} />
  </div>;
}

/** Preview a selected local image without uploading it before the user sends. */
function LocalPhoto({ file }: { file: File }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    const url = URL.createObjectURL(file);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return src ? <img className={styles.selectedPhoto} src={src} alt="" width={28} height={28} /> : null;
}

/** Show the files queued for this message with a way to remove any selection. */
export function SelectedAttachments({ attachments, disabled }: { attachments: ComposerAttachments; disabled: boolean }) {
  return <>
    {attachments.items.length ? <ul className={styles.files} aria-label="待发送附件">
      {attachments.items.map((item) => <li key={item.key} className={styles.selected}>
        {item.kind === "image" && item.file ? <LocalPhoto file={item.file} /> : null}
        <span>{item.name}</span>
        <button type="button" className={styles.remove} disabled={disabled}
          aria-label={`移除${item.name}`} onClick={() => attachments.remove(item.key)}><X size={15} /></button>
      </li>)}
    </ul> : null}
    {attachments.error ? <p className="caption red" role="alert">{attachments.error}</p> : null}
  </>;
}

/** Load a private original on demand and render it as text, without links, images or HTML. */
function MarkdownFilePreview({ attachment }: { attachment: InputAttachment }) {
  const [open, setOpen] = useState(false);
  const [document, setDocument] = useState<(InputAttachment & { text: string }) | null>(null);
  const [error, setError] = useState(false);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError(false);
    setDocument(null);
    readMarkdownAttachment(attachment.attachment_id, controller.signal)
      .then((file) => { if (!controller.signal.aborted) setDocument(file); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [attachment.attachment_id, open]);

  return <li>
    <button type="button" className="link-button" aria-expanded={open} aria-controls={id}
      onClick={() => setOpen((value) => !value)}>{open ? "收起" : "查看"} {attachment.filename ?? "原文件"}</button>
    {open ? <div id={id} className={styles.document}>
      {error ? <p role="alert">文件打不开，可能已经被删除。可以收起后再试。</p>
        : document ? <>
          <strong>{document.title}</strong>
          <p>已完成 {document.completed_count ?? 0} 项，未完成 {document.unfinished_count ?? 0} 项</p>
          <pre>{document.text}</pre>
        </> : <p role="status">正在打开文件…</p>}
    </div> : null}
  </li>;
}

/** Render only the account-scoped file preview or photo routes for saved attachments. */
export function AttachmentPreviews({ attachments }: { attachments?: InputAttachment[] }) {
  if (!attachments?.length) return null;
  return <ul className={styles.previews} aria-label="已保存附件">
    {attachments.map((file) => file.kind === "markdown"
      ? <MarkdownFilePreview key={file.attachment_id} attachment={file} />
      : <li key={file.attachment_id}><a className={styles.photo} target="_blank" rel="noreferrer"
        href={pa(`/image-attachments/${encodeURIComponent(file.attachment_id)}/original`)}>
        <img src={pa(`/image-attachments/${encodeURIComponent(file.attachment_id)}/thumbnail`)}
          alt="已保存图片" width={64} height={64} loading="lazy" />
        <span>查看原图</span>
      </a></li>)}
  </ul>;
}
