import { useEffect, useRef, type ReactNode } from "react";

import { ApiError, errorMessage } from "../api/client";
import { Icon } from "./Icon";

export function PageHeader({ eyebrow, title, actions }: { eyebrow?: ReactNode; title: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-header__titles">
        {eyebrow && <div className="page-header__eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  subtitle,
  aside,
  tone,
  children,
  as: Tag = "section",
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  aside?: ReactNode;
  tone?: "accent" | "warning" | "navy";
  children?: ReactNode;
  as?: "section" | "div" | "article";
  className?: string;
}) {
  return (
    <Tag className={`card ${tone ? `card--${tone}` : ""} ${className}`}>
      {(title || aside) && (
        <div className="card__head">
          <div className="stack" style={{ gap: 2 }}>
            {title && <h2 className="card__title">{title}</h2>}
            {subtitle && <p className="card__subtitle">{subtitle}</p>}
          </div>
          {aside && <div className="small muted">{aside}</div>}
        </div>
      )}
      {children}
    </Tag>
  );
}

export function Stat({
  label,
  value,
  unit,
  delta,
  deltaTone,
  children,
  tone,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  delta?: ReactNode;
  deltaTone?: "good" | "attention" | "neutral";
  children?: ReactNode;
  tone?: "accent";
}) {
  return (
    <div className={`card ${tone ? `card--${tone}` : ""}`}>
      <div className="stat">
        <div className="stat__label" style={tone ? { color: "var(--accent-soft-text)", fontWeight: 600 } : undefined}>
          {label}
        </div>
        <div className="stat__value">
          {value} {unit && <span className="stat__unit">{unit}</span>}
        </div>
        {delta && (
          <div className={`stat__delta ${deltaTone === "good" ? "delta-good" : deltaTone === "attention" ? "delta-attention" : "muted"}`}>
            {delta}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

/** Progress bar with an explicit text alternative (value / denominator). */
export function Meter({ value, max, label, target }: { value: number; max: number; label: string; target?: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const tpct = target !== undefined && max > 0 ? Math.min(100, (target / max) * 100) : undefined;
  return (
    <div className="meter" role="img" aria-label={label} style={{ position: "relative", overflow: "visible" }}>
      <div style={{ width: `${pct}%` }} />
      {tpct !== undefined && (
        <span aria-hidden style={{ position: "absolute", top: -4, bottom: -4, left: `${tpct}%`, width: 2, background: "var(--text)" }} />
      )}
    </div>
  );
}

export function Loading({ label = "Chargement…", lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div className="stack" aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const network = error instanceof ApiError && error.isNetwork;
  return (
    <div className="status status--critical" role="alert" style={{ alignItems: "flex-start" }}>
      <Icon name="alert" size={18} />
      <div className="stack" style={{ gap: 4, flex: 1 }}>
        <strong>{network ? "Serveur injoignable" : "Une erreur est survenue"}</strong>
        <span className="small">{errorMessage(error)}</span>
        {network && (
          <span className="xsmall">Lancez le backend (port 8000) puis réessayez. Voir frontend/README.md.</span>
        )}
        {error instanceof ApiError && error.requestId && <span className="xsmall">Réf. {error.requestId}</span>}
      </div>
      {onRetry && (
        <button type="button" className="btn btn--small" onClick={onRetry}>
          Réessayer
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <strong style={{ color: "var(--text-title)" }}>{title}</strong>
      {children && <p className="small">{children}</p>}
      {action}
    </div>
  );
}

/** Renders loading / error / content for a query result. */
export function QueryState<T>({
  query,
  children,
  lines,
}: {
  query: { data: T | undefined; isPending: boolean; error: unknown; refetch: () => unknown };
  children: (data: T) => ReactNode;
  lines?: number;
}) {
  if (query.isPending) return <Loading lines={lines} />;
  if (query.error) return <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children(query.data as T)}</>;
}

export function SubjectBadge({ acronym, color }: { acronym: string; color?: string }) {
  if (!acronym) return null;
  return (
    <span className="badge" style={{ color: "var(--text-title)", borderColor: color ?? "var(--border-meaningful)" }}>
      {acronym}
    </span>
  );
}

export function StatusLine({ tone, label, children }: { tone: "good" | "warning" | "critical"; label: string; children?: ReactNode }) {
  const icon = tone === "good" ? "check" : tone === "warning" ? "warning" : "alert";
  return (
    <div className={`status status--${tone}`}>
      <Icon name={icon} size={18} />
      <span className="strong">{label}</span>
      {children && <span className="muted">— {children}</span>}
    </div>
  );
}

export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title" ref={ref}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 id="dialog-title">{title}</h2>
          <button type="button" className="btn btn--ghost btn--small" onClick={onClose} aria-label="Fermer">
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function DemoNotice({ children }: { children: ReactNode }) {
  return <span className="pill pill--demo">{children}</span>;
}
