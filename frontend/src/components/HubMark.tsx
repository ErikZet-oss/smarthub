"use client";

import { useId } from "react";

type HubMarkProps = {
  className?: string;
  /** CSS px (width/height). */
  size?: number;
  /** Ak je nastavené, `role="img"` a skrytá dekorácia inak. */
  title?: string;
  /** Svetlé pozadie (favicon); bez neho transparentné (menu). */
  withBackground?: boolean;
};

const STROKE = "#4b5563";

/**
 * Hub / dátové spájanie — centrálna „databáza“, oblúky a satelitné uzly
 * (inšpirované sieťovou ikonou hub).
 */
export function HubMark({
  className,
  size = 32,
  title,
  withBackground = false,
}: HubMarkProps) {
  const uid = useId().replace(/:/g, "");
  const bodyGrad = `hub-body-${uid}`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 48 48"
      width={size}
      height={size}
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <defs>
        <linearGradient id={bodyGrad} x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#d4d0e8" />
          <stop offset="100%" stopColor="#9d96b8" />
        </linearGradient>
      </defs>
      {withBackground ? (
        <rect width="48" height="48" rx="10" fill="#ffffff" />
      ) : null}
      <g
        fill="none"
        stroke={STROKE}
        strokeWidth="1.35"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Oblúkové vetvy vľavo / vpravo */}
        <path d="M20 24 C14 24 11 19 8 12" />
        <path d="M20 24 C14 24 11 29 8 36" />
        <path d="M28 24 C34 24 37 19 40 12" />
        <path d="M28 24 C34 24 37 29 40 36" />
        {/* Bočné „pcb“ ohyby k malým uzlom */}
        <path d="M8 20 L5 20 L5 17" />
        <path d="M8 28 L5 28 L5 31" />
        <path d="M40 20 L43 20 L43 17" />
        <path d="M40 28 L43 28 L43 31" />
        {/* Priame väzby na hlavné uzly */}
        <path d="M24 19 L24 10" />
        <path d="M24 29 L24 38" />
        <path d="M18 22 L10 10" />
        <path d="M30 22 L38 10" />
        <path d="M18 26 L10 38" />
        <path d="M30 26 L38 38" />
      </g>
      {/* Centrálna databáza (valec) */}
      <ellipse
        cx="24"
        cy="19.2"
        rx="6.2"
        ry="2.6"
        fill={`url(#${bodyGrad})`}
        stroke={STROKE}
        strokeWidth="1.2"
      />
      <rect
        x="17.8"
        y="19.2"
        width="12.4"
        height="9.6"
        fill={`url(#${bodyGrad})`}
        stroke={STROKE}
        strokeWidth="1.2"
      />
      <ellipse
        cx="24"
        cy="28.8"
        rx="6.2"
        ry="2.6"
        fill="#8f88a8"
        stroke={STROKE}
        strokeWidth="1.2"
      />
      <line
        x1="18.5"
        y1="22.2"
        x2="29.5"
        y2="22.2"
        stroke="#6b7280"
        strokeWidth="0.9"
      />
      <line
        x1="18.5"
        y1="25.2"
        x2="29.5"
        y2="25.2"
        stroke="#6b7280"
        strokeWidth="0.9"
      />
      {/* Veľké satelitné uzly */}
      <circle cx="24" cy="8" r="3.4" fill="#93c5fd" stroke={STROKE} strokeWidth="1.2" />
      <circle cx="24" cy="40" r="3.4" fill="#cbd5e1" stroke={STROKE} strokeWidth="1.2" />
      <circle cx="9" cy="11" r="3.4" fill="#bae6fd" stroke={STROKE} strokeWidth="1.2" />
      <circle cx="39" cy="11" r="3.4" fill="#e2e8f0" stroke={STROKE} strokeWidth="1.2" />
      <circle cx="9" cy="37" r="3.4" fill="#cbd5e1" stroke={STROKE} strokeWidth="1.2" />
      <circle cx="39" cy="37" r="3.4" fill="#93c5fd" stroke={STROKE} strokeWidth="1.2" />
      {/* Malé bočné uzly */}
      <circle cx="5" cy="17" r="2.1" fill="#fcd34d" stroke={STROKE} strokeWidth="1" />
      <circle cx="5" cy="24" r="2.1" fill="#fb923c" stroke={STROKE} strokeWidth="1" />
      <circle cx="5" cy="31" r="2.1" fill="#fcd34d" stroke={STROKE} strokeWidth="1" />
      <circle cx="43" cy="17" r="2.1" fill="#fb923c" stroke={STROKE} strokeWidth="1" />
      <circle cx="43" cy="24" r="2.1" fill="#fcd34d" stroke={STROKE} strokeWidth="1" />
      <circle cx="43" cy="31" r="2.1" fill="#fb923c" stroke={STROKE} strokeWidth="1" />
    </svg>
  );
}
