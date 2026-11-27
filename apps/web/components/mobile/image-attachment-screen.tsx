"use client";

import { useEffect, useRef, useState } from "react";
import { ImageOff } from "lucide-react";

import { pa } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import { deleteImage, IMAGE_UPLOAD_LIMIT_BYTES, listImages, updateImage, uploadImage, type ImageAttachment } from "@/lib/mobile/image-attachments";
import { useAuth } from "./providers";
import { ConfirmDialog, LoadingCard, LoginRequired, PageShell, Sheet } from "./ui";
import styles from "./image-attachment.module.css";

/** Render one owned picture and the owner's notes without presenting guesses as extracted facts. */
function ImageReviewCard({ attachment, onChanged, onDelete }: {
  attachment: ImageAttachment;
  onChanged: (attachment: ImageAttachment) => void;
  onDelete: () => void;
}) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const [preview, setPreview] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [caption, setCaption] = useState(attachment.caption);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  /** Save the current owner's note without modifying or forwarding the photo. */
  async function saveCaption() {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      onChanged(await updateImage(attachment.attachment_id, { caption: caption.trim() }));
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "图片说明没能保存。");
    } finally {
      setSaving(false);
    }
  }

  return <article className={styles.card} aria-label="已保存的图片">
    <button type="button" className={styles.thumbnail} aria-label="查看原图"
      onClick={() => { setPreviewFailed(false); setPreview(true); }}>
      {thumbnailFailed ? <span className={styles.unavailable}><ImageOff size={22} aria-hidden="true" />图片没加载出来</span> :
        // eslint-disable-next-line @next/next/no-img-element
        <img src={pa(attachment.thumbnail_path)} alt="已上传图片的缩略图" loading="lazy" onError={() => setThumbnailFailed(true)} />}
      <span>查看原图</span>
    </button>
    <div className={styles.notes}>
      <p className="caption">上传于 {formatDateTime(attachment.created_at)}</p>
      <label className="field">
        <span className="field__label">图片说明</span>
        <textarea className="input" rows={3} value={caption} maxLength={300}
          onChange={(event) => { setCaption(event.target.value); setSaved(false); }} />
      </label>
      <div className={styles.actions}>
        <button type="button" className="btn btn--secondary btn--small"
          disabled={saving || caption.trim() === attachment.caption} onClick={() => void saveCaption()}>
          {saving ? "正在保存…" : "保存说明"}
        </button>
        <button type="button" className="link-button" onClick={onDelete}>删除图片</button>
      </div>
      {error ? <p role="alert" className="danger-text">{error}</p> : null}
      {saved ? <p role="status" className="caption">图片说明已保存</p> : null}
    </div>
    <Sheet title="查看原图" open={preview} onClose={() => setPreview(false)} stickyHeader>
      {previewFailed ? <p role="status" className="caption">原图没加载出来，关闭后可以再试一次。</p> :
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles.original} src={pa(attachment.original_path)} alt={attachment.caption || "上传的原图"}
          onError={() => setPreviewFailed(true)} />}
    </Sheet>
  </article>;
}

/** Keep image uploads, original previews and edits in the signed-in account. */
export function ImageAttachmentScreen({ matterId }: { matterId?: string }) {
  const { state, user } = useAuth();
  const title = matterId ? "事项图片" : "我的图片";
  return <PageShell title={title} fallbackHref={matterId ? "/matters/" + encodeURIComponent(matterId) : "/me"}>
    {state === "signedIn" && user?.user_id ? <OwnedImageCollection key={user.user_id} matterId={matterId} /> :
      <div className={styles.page}>
        {state === "anonymous" || state === "offline" ? <LoginRequired title="登录后查看图片" body="图片仅在你的账号中显示。" /> :
          <LoadingCard label="正在读取账号…" />}
      </div>}
  </PageShell>;
}

/** Remount the private collection when the current account changes. */
function OwnedImageCollection({ matterId }: { matterId?: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [images, setImages] = useState<ImageAttachment[]>([]);
  const [ready, setReady] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<ImageAttachment | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setReady(false);
    setImages([]);
    setError("");
    listImages(matterId, controller.signal).then(
      (items) => {
        if (controller.signal.aborted) return;
        setImages(items);
        setReady(true);
      },
      (cause) => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "图片暂时没加载出来。");
      },
    );
    return () => controller.abort();
  }, [matterId, retry]);

  /** Upload only after the user confirms the selected file. */
  async function savePhoto() {
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const attachment = await uploadImage(file, matterId);
      setImages((current) => [attachment, ...current]);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      setNotice(matterId ? "图片已保存到此事项" : "图片已保存");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "图片没能上传，可以再试一次。");
    } finally {
      setBusy(false);
    }
  }

  /** Remove the photo only after the owner confirms. */
  async function removePhoto() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setError("");
    setNotice("");
    try {
      await deleteImage(deleteTarget.attachment_id);
      setImages((current) => current.filter((image) => image.attachment_id !== deleteTarget.attachment_id));
      setNotice("图片已删除");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "图片没能删除。");
    } finally {
      setDeleteTarget(null);
      setDeleteBusy(false);
    }
  }

  return <div className={styles.page}>
    {ready ? <form className={styles.upload} onSubmit={(event) => { event.preventDefault(); void savePhoto(); }}>
      <label className="field">
        <span className="field__label">选择图片</span>
        <input ref={inputRef} type="file" aria-label="选择图片" accept="image/png,image/jpeg,image/webp" disabled={busy}
          onChange={(event) => {
            const selected = event.target.files?.[0] ?? null;
            setNotice("");
            if (selected && selected.size > IMAGE_UPLOAD_LIMIT_BYTES) {
              setError("图片不能超过 8 兆。");
              setFile(null);
              event.target.value = "";
            } else {
              setError("");
              setFile(selected);
            }
          }} />
      </label>
      <button type="submit" className="btn btn--primary btn--small" disabled={!file || busy}>
        {busy ? "正在上传…" : matterId ? "上传到此事项" : "上传图片"}
      </button>
    </form> : !error ? <LoadingCard label="正在读取图片…" /> : null}
    {error ? <p role="alert" className="danger-text">{error}</p> : null}
    {!ready && error ? <button className="btn btn--secondary btn--small" onClick={() => setRetry(retry + 1)}>重新读取</button> : null}
    {notice ? <p role="status" className="caption">{notice}</p> : null}
    {ready && images.length === 0 ? <p className="caption">还没有图片。选择一张图片就能上传。</p> : null}
    {images.map((attachment) => <ImageReviewCard key={attachment.attachment_id} attachment={attachment}
      onChanged={(updated) => setImages((current) => current.map((item) => item.attachment_id === updated.attachment_id ? updated : item))}
      onDelete={() => setDeleteTarget(attachment)} />)}
    <ConfirmDialog open={Boolean(deleteTarget)} title="删除图片？" body="图片和说明都会删除。"
      confirmLabel="删除图片" danger busy={deleteBusy} onCancel={() => setDeleteTarget(null)}
      onConfirm={() => void removePhoto()} />
  </div>;
}
