import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { ApiError, errorMessage } from "../api/client";
import { Icon } from "./Icon";

export function PageHeader({ eyebrow, title, actions }: { eyebrow?: ReactNode; title: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-header__titles">
        {eyebrow && <div className="page-header__eyebrow">{eyebrow}</div>}
        <h1 id="page-title" tabIndex={-1}>
          {title}
        </h1>
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
        <div key={i} className="skeleton" style={{ width: `${Math.max(30, 90 - i * 15)}%` }} />
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

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const container = ref.current;
    const initial = container?.querySelector<HTMLElement>("input, select, textarea, button");
    initial?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === "Tab" && container) {
        const focusables = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, []);

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCloseRef.current()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="btn btn--ghost btn--small" onClick={() => onCloseRef.current()} aria-label="Fermer">
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirmer",
  danger = false,
  requireText,
  pending = false,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  requireText?: string;
  pending?: boolean;
  error?: unknown;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [typed, setTyped] = useState("");
  const canConfirm = !pending && (!requireText || typed.trim() === requireText);
  return (
    <Dialog title={title} onClose={onClose}>
      <p className="small">{message}</p>
      {requireText && (
        <label className="field">
          <span>
            Tapez <code>{requireText}</code> pour confirmer
          </span>
          <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </label>
      )}
      {Boolean(error) && <div className="status status--critical">{errorMessage(error)}</div>}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn" onClick={onClose} disabled={pending}>
          Annuler
        </button>
        <button
          type="button"
          className={danger ? "btn btn--danger" : "btn btn--primary"}
          disabled={!canConfirm}
          onClick={onConfirm}
        >
          {pending ? "En cours…" : confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}

export function ExamBlockedCard({ feature }: { feature: string }) {
  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <PageHeader eyebrow="Mode examen actif" title={feature} />
      <Card tone="warning" title="Accès suspendu pendant l'examen">
        <p className="small">
          Pour garantir l'intégrité de l'épreuve en cours (règle R21), les notes, corrigés, documents et assistants IA sont
          désactivés jusqu'à la remise de votre copie.
        </p>
        <div className="row">
          <Link className="btn btn--primary" to="/quiz">
            Retourner à mon examen
          </Link>
          <Link className="btn" to="/">
            Dashboard
          </Link>
        </div>
      </Card>
    </div>
  );
}

export function DemoNotice({ children }: { children: ReactNode }) {
  return <span className="pill pill--demo">{children}</span>;
}
