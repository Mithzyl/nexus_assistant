"use client";

/** Full-width capsule buttons matching the iOS primary/secondary styles. */

import type { ReactNode } from "react";
import { ArrowRight } from "lucide-react";

export function PrimaryButton({
  children,
  onClick,
  disabled,
  busy,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  busy?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      className="btn btn--primary"
      onClick={onClick}
      disabled={disabled || busy}
    >
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      <span>{children}</span>
      {!busy ? <ArrowRight size={14} strokeWidth={2.4} aria-hidden="true" /> : null}
    </button>
  );
}

export function SecondaryButton({
  children,
  onClick,
  disabled,
  type = "button",
  danger = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
  danger?: boolean;
}) {
  return (
    <button
      type={type}
      className={`btn ${danger ? "btn--danger" : "btn--secondary"}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
