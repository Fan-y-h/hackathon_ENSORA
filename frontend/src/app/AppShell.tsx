import { NavLink, Outlet } from "react-router-dom";

import { useExamMode, useNotifications, useProfile } from "../api/queries";
import { BrandLogo } from "../components/BrandLogo";
import { Icon } from "../components/Icon";
import "./shell.css";

interface NavItem {
  to: string;
  label: string;
  planned?: boolean;
}

/** Same three groups as the mock-up sidebar; planned modules stay visible but labelled. */
export const NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "Organisation",
    items: [
      { to: "/", label: "Dashboard" },
      { to: "/subjects", label: "Mes matières", planned: true },
      { to: "/library", label: "Bibliothèque", planned: true },
      { to: "/planner", label: "Smart Planner" },
      { to: "/tasks", label: "Mes tâches" },
    ],
  },
  {
    title: "Apprentissage",
    items: [
      { to: "/tutor", label: "AI Tutor", planned: true },
      { to: "/studio", label: "Learning Studio", planned: true },
      { to: "/flashcards", label: "Flashcards" },
      { to: "/quiz", label: "Quiz" },
      { to: "/exams", label: "Exam Trainer", planned: true },
      { to: "/notes", label: "Mes notes" },
    ],
  },
  {
    title: "Suivi",
    items: [
      { to: "/focus", label: "Focus Room" },
      { to: "/analytics", label: "Learning Analytics" },
      { to: "/grades", label: "Résultats & objectifs" },
    ],
  },
];

export function AppShell() {
  const notifications = useNotifications();
  const profile = useProfile();
  const exam = useExamMode();
  const unread = notifications.data?.unread_count ?? 0;
  const examActive = exam.data?.active ?? false;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Aller au contenu
      </a>
      <nav className="sidebar" aria-label="Navigation principale">
        <NavLink to="/" className="sidebar__brand" aria-label="Ensora+, retour au Dashboard">
          <BrandLogo />
        </NavLink>
        {NAV.map((group) => (
          <div key={group.title} className="sidebar__group">
            <div className="sidebar__title">{group.title}</div>
            {group.items
              .filter((item) => !item.planned)
              .map((item) => {
                const locked = examActive && ["/notes"].includes(item.to);
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/"}
                    className="sidebar__link"
                    aria-disabled={locked || undefined}
                    onClick={(e) => locked && e.preventDefault()}
                  >
                    <span className="sidebar__dot" aria-hidden="true" />
                    <span>{item.label}</span>
                    {locked && <Icon name="lock" size={14} label="Bloqué pendant l'examen" />}
                  </NavLink>
                );
              })}
          </div>
        ))}
        {/* Modules without a backend yet: one collapsed entry instead of five "à venir" tags. */}
        <details className="sidebar__soon">
          <summary className="sidebar__title">
            Bientôt <Icon name="chevronDown" size={12} />
          </summary>
          {NAV.flatMap((g) => g.items)
            .filter((item) => item.planned)
            .map((item) => (
              <NavLink key={item.to} to={item.to} className="sidebar__link sidebar__link--soon">
                <span className="sidebar__dot" aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            ))}
        </details>
        <div className="sidebar__group sidebar__footer">
          <NavLink to="/notifications" className="sidebar__link">
            <Icon name="bell" />
            <span>Notifications</span>
            {unread > 0 && (
              <span className="sidebar__count" aria-label={`${unread} non lues`}>
                {unread}
              </span>
            )}
          </NavLink>
          <NavLink to="/settings" className="sidebar__link">
            <span className="sidebar__dot" aria-hidden="true" />
            <span>{profile.data?.display_name || "Mon profil"}</span>
          </NavLink>
        </div>
      </nav>
      <main id="main" className="shell__main" tabIndex={-1}>
        {examActive && (
          <div className="exam-banner" role="status">
            <Icon name="clock" /> Mode examen : notes et corrigés bloqués
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
