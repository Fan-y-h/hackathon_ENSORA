import { Monitor, Moon, Sun } from "lucide-react";

import { setThemePreference, useThemePreference, type ThemePreference } from "@/app/theme";

const NEXT: Record<ThemePreference, ThemePreference> = { system: "light", light: "dark", dark: "system" };

const LABELS: Record<ThemePreference, string> = {
  system: "Thème : automatique",
  light: "Thème : clair",
  dark: "Thème : sombre",
};

const ICONS = { system: Monitor, light: Sun, dark: Moon };

/** One button that cycles automatic -> light -> dark. The choice is remembered. */
export function ThemeToggle() {
  const preference = useThemePreference();
  const Icon = ICONS[preference];
  const next = NEXT[preference];

  return (
    <button
      type="button"
      onClick={() => setThemePreference(next)}
      className="focus-ring rounded-md border border-line p-2 hover:bg-surface-2"
      aria-label={`${LABELS[preference]}. Passer en ${LABELS[next].replace("Thème : ", "")}`}
      title={LABELS[preference]}
    >
      <Icon className="size-5" aria-hidden="true" />
    </button>
  );
}
