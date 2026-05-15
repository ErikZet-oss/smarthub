"use client";

import { useId } from "react";

type HubMarkProps = {
  className?: string;
  /** CSS px (width/height). */
  size?: number;
  /** Ak je nastavené, `role="img"` a skrytá dekorácia inak. */
  title?: string;
};

/**
 * Značka „hub“ — stredový uzol so štyrmi spojeniami (spájanie dodávateľov / dát).
 * Používa sa v UI; favicon je samostatný `app/icon.svg`.
 */
export function HubMark({ className, size = 32, title }: HubMarkProps) {
  const uid = useId().replace(/:/g, "");
  const gradId = `hub-grad-${uid}`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <defs>
        <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#7dd3fc" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>
      </defs>
      {/* Štyri spojnice do satelitov */}
      <line
        x1="16"
        y1="16"
        x2="16"
        y2="7.5"
        stroke="#64748b"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <line
        x1="16"
        y1="16"
        x2="24.5"
        y2="16"
        stroke="#64748b"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <line
        x1="16"
        y1="16"
        x2="16"
        y2="24.5"
        stroke="#64748b"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <line
        x1="16"
        y1="16"
        x2="7.5"
        y2="16"
        stroke="#64748b"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="16" cy="6" r="3" fill="#94a3b8" />
      <circle cx="26" cy="16" r="3" fill="#94a3b8" />
      <circle cx="16" cy="26" r="3" fill="#94a3b8" />
      <circle cx="6" cy="16" r="3" fill="#94a3b8" />
      <circle cx="16" cy="16" r="6" fill={`url(#${gradId})`} />
      <circle cx="16" cy="16" r="2.5" fill="#f0f9ff" opacity="0.9" />
    </svg>
  );
}
