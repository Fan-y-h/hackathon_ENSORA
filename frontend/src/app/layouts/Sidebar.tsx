import { LogOut, X } from "lucide-react";
import { NavLink, useNavigate } from "react-router";

import { useLogout } from "@/api/hooksWorkspace";

import { APP_ROUTES, NAV_GROUPS } from "@/app/routes";
import { EnsaiLogo } from "@/components/EnsaiLogo";
import { EnsoraLogo } from "@/components/EnsoraLogo";

interface SidebarProps {
  /** Only used on small screens, where the sidebar slides in. */
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const logout = useLogout();
  const navigate = useNavigate();
  return (
    <>
      {/* Dark overlay behind the menu on small screens; a click closes the menu. */}
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={onClose} aria-hidden="true" />}

      <aside
        className={[
          "fixed inset-y-0 left-0 z-40 w-64 bg-sidebar text-sidebar-ink transition-transform duration-200",
          "lg:sticky lg:top-0 lg:z-auto lg:h-dvh lg:w-auto lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        ].join(" ")}
      >
        <div className="flex h-full flex-col gap-5 overflow-y-auto px-3 py-4">
          <div className="flex items-center justify-between px-2">
            {/* The club logo already contains the name, so no text next to it. */}
            <NavLink to="/" aria-label="ENSORA+, tableau de bord" className="focus-ring rounded-lg">
              <EnsoraLogo className="w-44" />
            </NavLink>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md p-1.5 text-sidebar-muted hover:bg-sidebar-active hover:text-white lg:hidden"
              aria-label="Fermer le menu"
            >
              <X className="size-5" aria-hidden="true" />
            </button>
          </div>

          <nav aria-label="Navigation principale" className="flex flex-col gap-4">
            {NAV_GROUPS.map((group) => {
              const items = APP_ROUTES.filter((route) => route.group === group.id);
              return (
                <div key={group.id}>
                  <p className="mb-1 px-2.5 text-[11px] font-semibold tracking-wider text-sidebar-muted uppercase">
                    {group.label}
                  </p>
                  <ul className="flex flex-col gap-0.5">
                    {items.map((route) => {
                      const Icon = route.icon;
                      const isLater = route.priority === "later";
                      return (
                        <li key={route.path}>
                          <NavLink
                            to={route.path}
                            end={route.path === "/"}
                            className={({ isActive }) =>
                              [
                                "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                                "focus-visible:outline-2 focus-visible:outline-accent",
                                isActive
                                  ? "bg-sidebar-active text-white shadow-[inset_3px_0_0_var(--accent)]"
                                  : "hover:bg-sidebar-active hover:text-white",
                                isLater ? "text-sidebar-muted" : "",
                              ].join(" ")
                            }
                          >
                            <Icon className="size-4 shrink-0" aria-hidden="true" />
                            <span className="truncate">{route.label}</span>
                            {isLater && (
                              <span className="ml-auto rounded-full border border-sidebar-active px-1.5 text-[10px]">
                                plus tard
                              </span>
                            )}
                          </NavLink>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </nav>

          {/* mt-auto pushes the school logo to the bottom of the sidebar. */}
          <div className="mt-auto flex flex-col gap-3 px-2 pt-2">
            <button
              type="button"
              onClick={() => logout.mutate(undefined, { onSuccess: () => void navigate("/login", { replace: true }) })}
              className="focus-ring flex items-center gap-2.5 rounded-md px-0.5 py-1.5 text-sm text-sidebar-muted hover:text-white"
            >
              <LogOut className="size-4" aria-hidden="true" />
              Se déconnecter
            </button>
            <EnsaiLogo className="w-28" />
          </div>
        </div>
      </aside>
    </>
  );
}
