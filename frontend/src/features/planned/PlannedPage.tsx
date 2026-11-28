import { Link } from "react-router-dom";

import { Card, PageHeader } from "../../components/ui";

/**
 * Modules whose backend does not exist yet (catalog, ingestion, RAG, AI providers).
 * Shown as "planned", never simulated (docs/FRONTEND_UX_UI.md, MVP UX acceptance).
 */
const MODULES = {
  subjects: { title: "Mes matières", body: "Espace par matière : chapitres, notions, cours, TD, TP, annales et supports générés.", owner: "catalogue (C1)" },
  library: { title: "Bibliothèque", body: "Documents synchronisés depuis Moodle, le Drive Ensora et vos dossiers, recherche plein texte et sémantique.", owner: "ingestion + RAG" },
  tutor: { title: "AI Tutor", body: "Assistant connecté à vos cours, avec citations vers la page d'origine.", owner: "moteur IA et RAG" },
  studio: { title: "Learning Studio", body: "Fiches, podcasts, vidéos, cartes mentales générés à la demande, uniquement quand vous démarrez l'activité.", owner: "moteur IA" },
  exams: { title: "Exam Trainer", body: "Annales, examens blancs et entraînement chronométré. Le mode examen des quiz est déjà disponible.", owner: "learning + catalogue" },
  notFound: { title: "Page introuvable", body: "Cette adresse ne correspond à aucun écran.", owner: "" },
} as const;

export function PlannedPage({ module }: { module: keyof typeof MODULES }) {
  const m = MODULES[module];
  return (
    <div className="page" style={{ maxWidth: 800 }}>
      <PageHeader eyebrow={module === "notFound" ? "Erreur 404" : "Module à venir"} title={m.title} />
      <Card>
        <p>{m.body}</p>
        {m.owner && (
          <p className="small muted">
            En attente du backend : {m.owner}. Cet écran n'affiche volontairement aucune donnée simulée.
          </p>
        )}
        <div className="row">
          <Link className="btn" to="/">
            Retour au Dashboard
          </Link>
          {module === "exams" && (
            <Link className="btn btn--primary" to="/quiz">
              Ouvrir les quiz
            </Link>
          )}
        </div>
      </Card>
    </div>
  );
}
