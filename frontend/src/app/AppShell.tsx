import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useExamMode, useNotifications, useProfile } from "../api/queries";
import { BrandLogo } from "../components/BrandLogo";
import { Icon } from "../components/Icon";
import "./shell.css";

interface NavItem {
  to: string;
  label: string;
}

export const NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "Organisation",
    items: [
      { to: "/", label: "Dashboard" },
      { to: "/subjects", label: "Mes matières" },
      { to: "/library", label: "Bibliothèque" },
      { to: "/planner", label: "Smart Planner" },
      { to: "/tasks", label: "Mes tâches" },
    ],
  },
  {
    title: "Apprentissage",
    items: [
      { to: "/tutor", label: "AI Tutor" },
      { to: "/studio", label: "Learning Studio" },
      { to: "/flashcards", label: "Flashcards" },
      { to: "/quiz", label: "Quiz" },
      { to: "/exams", label: "Exam Trainer" },
      { to: "/notes", label: "Mes notes" },
    ],
  },
  {
    title: "Suivi",
    items: [
      { to: "/focus", label: "Focus Room" },
      { to: "/analytics", label: "Learning Analytics" },
      { to: "/grades", label: "Mes résultats" },
    ],
  },
];

const examRestricted = ["/notes", "/library", "/tutor", "/studio", "/flashcards"];

export function AppShell() {
  const { pathname } = useLocation();
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
            {group.items.map((item) => {
                const locked = examActive && examRestricted.includes(item.to);
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
        {import.meta.env.VITE_DEMO_MODE === "true" && <p className="exam-banner">Démonstration · Données synthétiques</p>}
        {examActive && (
          <div className="exam-banner" role="status">
            <Icon name="clock" /> Mode examen : notes et corrigés bloqués
          </div>
        )}
        {examRestricted.some((path) => pathname === path || pathname.startsWith(`${path}/`)) && (examActive || exam.isPending || exam.isError)
          ? <h1>{examActive ? "Accès bloqué pendant l’examen" : "Vérification du mode examen"}</h1>
          : <Outlet />}
      </main>
    </div>
  );
}
