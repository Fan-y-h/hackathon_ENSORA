/**
 * Display preferences persisted in the browser (theme, accessible mode, subject colours).
 * They only change presentation: nothing here is sent to the API.
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";

export interface DisplayPreferences {
  theme: ThemeChoice;
  accessible: boolean;
  subjectColors: Record<string, string>;
  alwaysShowAcronyms: boolean;
}

// v2: the default theme became "light" (mock-up reference). v1 values are kept except the theme.
const STORAGE_KEY = "ensora.display.v2";
const LEGACY_KEY = "ensora.display.v1";

const DEFAULTS: DisplayPreferences = {
  theme: "light",
  accessible: false,
  subjectColors: {},
  alwaysShowAcronyms: true,
};

function load(): DisplayPreferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<DisplayPreferences>) };
    const legacy = localStorage.getItem(LEGACY_KEY);
    if (!legacy) return DEFAULTS;
    const { theme, ...rest } = JSON.parse(legacy) as Partial<DisplayPreferences>;
    return { ...DEFAULTS, ...rest, ...(theme && theme !== "system" ? { theme } : {}) };
  } catch {
    return DEFAULTS;
  }
}

interface Ctx extends DisplayPreferences {
  resolvedDark: boolean;
  update: (patch: Partial<DisplayPreferences>) => void;
}

const PreferencesContext = createContext<Ctx | null>(null);

function useSystemDark(): boolean {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const mql = window.matchMedia?.(query);
    if (!mql) return;
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return dark;
}

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<DisplayPreferences>(load);
  const systemDark = useSystemDark();
  const resolvedDark = prefs.theme === "dark" || (prefs.theme === "system" && systemDark);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  }, [prefs]);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = resolvedDark ? "dark" : "light";
    root.dataset.contrast = prefs.accessible ? "accessible" : "standard";
    root.style.colorScheme = resolvedDark ? "dark" : "light";
  }, [resolvedDark, prefs.accessible]);

  const value = useMemo<Ctx>(
    () => ({ ...prefs, resolvedDark, update: (patch) => setPrefs((p) => ({ ...p, ...patch })) }),
    [prefs, resolvedDark],
  );
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): Ctx {
  const ctx = useContext(PreferencesContext);
  if (!ctx) throw new Error("usePreferences must be used inside PreferencesProvider");
  return ctx;
}
