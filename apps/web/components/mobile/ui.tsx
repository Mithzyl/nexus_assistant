"use client";

/** Shared mobile visual components matching the iOS Theme.swift anatomy. */

import {
  useEffect,
  useId,
  useRef,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

export function ScreenHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <header className="screen-header">
      <div className="screen-header__text">
        <h1 className="screen-title">{title}</h1>
        {subtitle ? <p className="screen-header__sub">{subtitle}</p> : null}
      </div>
      {action}
    </header>
  );
}

/** Sticky pushed-page shell with the circular glass back control. */
export function PageShell({
  title,
  subtitle,
  action,
  children,
  fallbackHref = "/today",
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  fallbackHref?: string;
}) {
  const router = useRouter();

  const goBack = () => {
    if (typeof window !== "undefined" && window.history.length > 1) {
      router.back();
    } else {
      router.push(fallbackHref);
    }
  };

  return (
    <div style={{ minHeight: "100dvh" }}>
      <div className="page-bar">
        <button
          type="button"
          className="icon-button"
          onClick={goBack}
          aria-label="返回"
        >
          <ChevronLeft size={19} strokeWidth={2.2} />
        </button>
        <p className="page-bar__title">{title}</p>
        {action}
      </div>
      {subtitle ? (
        <p
          className="caption"
          style={{ margin: "12px var(--m-gutter) 0" }}
        >
          {subtitle}
        </p>
      ) : null}
      <div className="page-shell">{children}</div>
    </div>
  );
}

export function SectionHeading({
  title,
  trailing,
}: {
  title: string;
  trailing?: string;
}) {
  return (
    <div
      className="flex items-center justify-between gap-12"
      style={{ marginBottom: 10 }}
    >
      <h2 className="section-title">{title}</h2>
      {trailing ? <span className="micro nowrap">{trailing}</span> : null}
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "fog" | "amber" | "green" | "red";
}) {
  return (
    <span className={`pill${tone === "neutral" ? "" : ` pill--${tone}`}`}>
      {children}
    </span>
  );
}

export function Notice({
  title,
  children,
  tone = "mist",
}: {
  title?: string;
  children: ReactNode;
  tone?: "mist" | "amber" | "green" | "red";
}) {
  const className =
    tone === "mist" ? "notice" : `notice notice--${tone}`;
  return (
    <div className={className}>
      {title ? <strong style={{ display: "block", marginBottom: 2 }}>{title}</strong> : null}
      {children}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  children,
}: {
  title: string;
  body: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      <p className="empty__body">{body}</p>
      {children ? <div className="mt-12">{children}</div> : null}
    </div>
  );
}

export function LoadingCard({ label = "正在读取…" }: { label?: string }) {
  return (
    <div className="card card--tight">
      <div className="flex items-center gap-10">
        <span className="spinner" aria-hidden="true" />
        <span className="caption" style={{ margin: 0 }}>
          {label}
        </span>
      </div>
    </div>
  );
}

export function ErrorCard({
  message,
  onRetry,
  retryable = true,
}: {
  message: string;
  onRetry?: () => void;
  retryable?: boolean;
}) {
  return (
    <div className="card card--red">
      <p className="card-title red" style={{ fontSize: 15 }}>
        没能读取
      </p>
      <p className="caption red" style={{ margin: "4px 0 0", opacity: 0.9 }}>
        {message}
      </p>
      {retryable && onRetry ? (
        <button
          type="button"
          className="link-button"
          onClick={onRetry}
          style={{ color: "var(--m-red)" }}
        >
          重试
        </button>
      ) : null}
    </div>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <label className="field" htmlFor={htmlFor}>
      <span className="field__label">{label}</span>
      {children}
      {hint ? <span className="field__hint">{hint}</span> : null}
    </label>
  );
}

export function SwitchRow({
  title,
  detail,
  checked,
  onChange,
  disabled,
  busy,
}: {
  title: string;
  detail?: string;
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  const id = useId();
  return (
    <div className="switch-row">
      <div className="grow">
        <p className="card-title" style={{ fontSize: 15 }} id={`${id}-title`}>
          {title}
        </p>
        {detail ? (
          <p className="caption" style={{ margin: "2px 0 0" }}>
            {detail}
          </p>
        ) : null}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-title`}
        className="switch"
        onClick={onChange}
        disabled={disabled || busy}
      />
    </div>
  );
}

/** Accessible confirmation overlay (replaces native alert/confirmationDialog). */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel = "取消",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="sheet-backdrop"
      style={{ alignItems: "center", padding: 24, zIndex: 100 }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        style={{
          width: "100%",
          maxWidth: 330,
          padding: 20,
          borderRadius: 18,
          background: "var(--m-card)",
        }}
      >
        <p className="card-title" id="confirm-title">
          {title}
        </p>
        <p className="caption" id="confirm-body" style={{ margin: "6px 0 18px" }}>
          {body}
        </p>
        <div className="flex gap-10">
          <button type="button" className="btn btn--secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`btn ${danger ? "btn--danger" : "btn--primary"}`}
            ref={confirmRef}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Full-height sheet used for long forms on small screens. */
export function Sheet({
  title,
  open,
  onClose,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    document.body.classList.add("m-sheet-open");
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("m-sheet-open");
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="sheet-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="sheet-handle" />
        <div className="flex items-center gap-10" style={{ marginBottom: 14 }}>
          <p className="card-title grow" style={{ margin: 0 }}>
            {title}
          </p>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭"
            style={{ width: 40, height: 40 }}
          >
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Inline list row with optional leading icon and trailing meta. */
export function LinkRow({
  href,
  onClick,
  icon,
  title,
  detail,
  meta,
  trailing,
  disabled,
}: {
  href?: string;
  onClick?: () => void;
  icon?: ReactNode;
  title: string;
  detail?: string;
  meta?: string;
  trailing?: ReactNode;
  disabled?: boolean;
}) {
  const content = (
    <>
      {icon ? <span className="tile tile--sm">{icon}</span> : null}
      <span className="row__body">
        <span className="row__title" style={{ display: "block" }}>
          {title}
        </span>
        {detail ? <span className="row__detail" style={{ display: "block" }}>{detail}</span> : null}
      </span>
      {meta ? <span className="row__meta">{meta}</span> : null}
      {trailing ?? (disabled ? null : <ChevronRight className="chevron" size={16} />)}
    </>
  );

  if (href) {
    return (
      <a className="row" href={href}>
        {content}
      </a>
    );
  }
  return (
    <button
      type="button"
      className="row"
      onClick={onClick}
      disabled={disabled}
      style={disabled ? { cursor: "default" } : undefined}
    >
      {content}
    </button>
  );
}
