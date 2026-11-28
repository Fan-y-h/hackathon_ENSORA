import { errorMessage } from "../../api/client";
import {
  SOURCE_LABEL,
  sourcesUnavailable,
  useConnectionAction,
  useConnections,
  useConsent,
  type ConnectionStatus,
} from "../../api/sources";
import { Card, EmptyState, ErrorPanel, Loading } from "../../components/ui";
import { formatLongDate, dayKey } from "../../lib/time";
import "../library/library.css";

const STATUS: Record<ConnectionStatus, { label: string; tone: "good" | "warning" | "critical" | "neutral" }> = {
  CONNECTED: { label: "Connecté", tone: "good" },
  NOT_CONFIGURED: { label: "Non connecté", tone: "neutral" },
  REAUTH_REQUIRED: { label: "À reconnecter", tone: "warning" },
  ACCESS_DENIED: { label: "Accès refusé", tone: "critical" },
  UNAVAILABLE: { label: "Indisponible", tone: "warning" },
  ERROR: { label: "Erreur", tone: "critical" },
};

/** Read-only connections to Moodle, Pamplemousse, Drive Ensora, Google Agenda and local files. */
export function SourcesCard() {
  const connections = useConnections();
  const consent = useConsent();
  const action = useConnectionAction();
  const busyId = action.isPending ? action.variables?.id : undefined;

  return (
    <Card title="Sources">
      {connections.isPending ? (
        <Loading lines={4} />
      ) : connections.error ? (
        sourcesUnavailable(connections.error) ? (
          <EmptyState title="Sources indisponibles" />
        ) : (
          <ErrorPanel error={connections.error} onRetry={() => void connections.refetch()} />
        )
      ) : (
        <div>
          {connections.data!.items
            .filter((c) => c.source !== "academic_import")
            .map((c) => {
              const status = STATUS[c.status] ?? STATUS.ERROR;
              const connected = c.status === "CONNECTED";
              return (
                <div key={c.id} className="sources-row">
                  <span className="stack" style={{ gap: 0 }}>
                    <strong>{SOURCE_LABEL[c.source]}</strong>
                    {c.last_success_at && (
                      <span className="xsmall muted">Synchro : {formatLongDate(dayKey(c.last_success_at)).toLowerCase()}</span>
                    )}
                  </span>
                  <span className={`pill${status.tone === "neutral" ? "" : ` status--${status.tone}`}`}>{status.label}</span>
                  <label className="checkbox" style={{ alignItems: "center" }}>
                    <input
                      type="checkbox"
                      checked={c.consent.read}
                      disabled={consent.isPending}
                      onChange={(e) => consent.mutate({ id: c.id, purpose: "read", granted: e.target.checked })}
                    />
                    Lecture
                  </label>
                  <button
                    type="button"
                    className="btn btn--small"
                    disabled={!c.consent.read || busyId === c.id}
                    title={c.consent.read ? undefined : "Autorisez d'abord la lecture"}
                    onClick={() =>
                      action.mutate({ id: c.id, action: connected ? "validate" : c.status === "REAUTH_REQUIRED" ? "reauthorize" : "authorize" })
                    }
                  >
                    {busyId === c.id ? "…" : connected ? "Vérifier" : "Connecter"}
                  </button>
                </div>
              );
            })}
          {(consent.error || action.error) && (
            <div className="status status--critical small">{errorMessage(consent.error ?? action.error)}</div>
          )}
          {action.isPending && <p className="xsmall muted">Une fenêtre de connexion ENSAI peut s'ouvrir.</p>}
        </div>
      )}
    </Card>
  );
}
