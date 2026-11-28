import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <section className="max-w-xl rounded-xl border border-line bg-surface p-6">
      <h1 id="page-title" className="mb-2 text-2xl font-semibold tracking-tight">
        Page introuvable
      </h1>
      <p className="mb-4 text-muted">Cette adresse ne correspond à aucune page d'ENSORA+.</p>
      <Link to="/" className="inline-flex rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-accent-ink">
        Retour au tableau de bord
      </Link>
    </section>
  );
}
