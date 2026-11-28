import { Bell, Menu, Search, UserRound } from "lucide-react";
import { Link, useNavigate } from "react-router";

import { useNotifications } from "@/api/hooksWorkspace";
import { USE_MOCKS } from "@/app/config";
import { ThemeToggle } from "@/components/ui";

interface TopbarProps {
  onMenuClick: () => void;
}

export function Topbar({ onMenuClick }: TopbarProps) {
  const navigate = useNavigate();
  const notifications = useNotifications();
  const unread = notifications.data?.filter((n) => !n.read_at).length ?? 0;
  return (
    <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-surface px-4 py-3 sm:px-6">
      <button
        type="button"
        onClick={onMenuClick}
        className="focus-ring rounded-md border border-line p-2 lg:hidden"
        aria-label="Ouvrir le menu"
      >
        <Menu className="size-5" aria-hidden="true" />
      </button>

      {/* Global search: opens the Library with the results. */}
      <form
        role="search"
        className="relative max-w-xl flex-1"
        onSubmit={(event) => {
          event.preventDefault();
          const q = new FormData(event.currentTarget).get("q")?.toString().trim();
          if (q) void navigate(`/library?q=${encodeURIComponent(q)}`);
        }}
      >
        <label>
          <span className="sr-only">Rechercher dans les documents</span>
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
          <input
            type="search"
            name="q"
            placeholder="Rechercher dans vos cours…"
            className="focus-ring w-full rounded-lg border border-transparent bg-surface-2 py-2 pr-3 pl-9 text-sm placeholder:text-muted"
          />
        </label>
      </form>

      {/* Demo label only. In production nothing is shown here (sync state lives in Paramètres). */}
      {USE_MOCKS && (
        <p className="hidden items-center gap-2 text-sm text-muted md:flex">
          <span className="size-2 rounded-full bg-warning" aria-hidden="true" />
          Données de démonstration
        </p>
      )}

      <ThemeToggle />
      <Link to="/notifications" className="focus-ring relative rounded-md border border-line p-2 hover:bg-surface-2" aria-label={unread ? `Notifications, ${unread} non lue${unread > 1 ? "s" : ""}` : "Notifications"}>
        <Bell className="size-5" aria-hidden="true" />
        {unread > 0 && (
          <span className="absolute -top-1.5 -right-1.5 grid min-w-5 place-items-center rounded-full bg-danger px-1 text-[11px] font-semibold text-danger-ink" aria-hidden="true">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Link>
      <Link
        to="/profile"
        className="focus-ring grid size-9 place-items-center rounded-full bg-accent text-accent-ink"
        aria-label="Mon profil"
      >
        {/* Generic icon until the real profile is loaded from the API: no invented initials. */}
        <UserRound className="size-5" aria-hidden="true" />
      </Link>
    </header>
  );
}
