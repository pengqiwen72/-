"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/** 共用的小组件。刻意保持朴素——界面里最该被看见的是画面描述本身。 */

export function Chip({
  children,
  tone = "default",
  title,
}: {
  children: ReactNode;
  tone?: "default" | "warn" | "ok" | "accent" | "demo";
  title?: string;
}) {
  const tones = {
    default: "bg-panel3 text-muted border-line2",
    warn: "bg-danger/15 text-danger border-danger/40",
    ok: "bg-ok/15 text-ok border-ok/40",
    accent: "bg-accent/15 text-accent border-accent/40",
    demo: "bg-accent/15 text-accent border-accent/50",
  } as const;
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] leading-none whitespace-nowrap ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = "ghost",
  title,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "ghost" | "danger";
  title?: string;
  className?: string;
}) {
  const variants = {
    primary:
      "bg-accent text-ink font-medium hover:bg-accent/90 disabled:bg-panel3 disabled:text-faint",
    ghost:
      "bg-panel2 text-muted border border-line hover:border-line2 hover:text-fg disabled:text-faint disabled:hover:border-line",
    danger:
      "bg-panel2 text-faint border border-line hover:text-danger hover:border-danger/50",
  } as const;
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1 rounded px-2 py-1 text-xs transition-colors disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/**
 * 就地编辑的文本。
 * 分镜师改描述时不该被弹窗打断思路，所以点击直接变输入框，
 * Esc 撤销、失焦保存。
 */
export function EditableText({
  value,
  onCommit,
  placeholder = "—",
  multiline = false,
  className = "",
  inputClassName = "",
}: {
  value: string;
  onCommit: (next: string) => void;
  placeholder?: string;
  multiline?: boolean;
  className?: string;
  inputClassName?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement>(null);

  useEffect(() => {
    if (editing && ref.current) {
      ref.current.focus();
      if (ref.current instanceof HTMLTextAreaElement) {
        ref.current.style.height = "auto";
        ref.current.style.height = `${ref.current.scrollHeight}px`;
      }
    }
  }, [editing, draft]);

  function commit() {
    setEditing(false);
    const next = draft.trim();
    if (next !== value) onCommit(next);
  }

  if (editing) {
    const shared = {
      value: draft,
      onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
      onBlur: commit,
      placeholder,
      className: `w-full resize-none rounded border border-accent/60 bg-ink px-1.5 py-1 text-inherit ${inputClassName}`,
      onKeyDown: (e: React.KeyboardEvent) => {
        if (e.key === "Escape") {
          setDraft(value);
          setEditing(false);
        } else if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          commit();
        }
      },
    };
    return multiline ? (
      <textarea {...shared} ref={ref as React.RefObject<HTMLTextAreaElement>} rows={2} />
    ) : (
      <input {...shared} ref={ref as React.RefObject<HTMLInputElement>} />
    );
  }

  return (
    <span
      role="button"
      tabIndex={0}
      title="点击编辑"
      onClick={() => {
        setDraft(value);
        setEditing(true);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setDraft(value);
          setEditing(true);
        }
      }}
      className={`block cursor-text rounded border border-transparent px-1.5 py-1 transition-colors hover:border-line hover:bg-panel2/60 ${
        value ? "" : "text-faint italic"
      } ${className}`}
    >
      {value || placeholder}
    </span>
  );
}
