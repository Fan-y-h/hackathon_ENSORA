// Theme preference (system / light / dark), saved in the browser.
// The CSS reads data-theme on <html>; index.html applies it before React starts
// to avoid a white flash. Keep STORAGE_KEY in sync with index.html.
import { useSyncExternalStore } from "react";

export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "ensora.theme";
const listeners = new Set<() => void>();

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    // Storage can be blocked (private window): fall back to the system setting.
    return "system";
  }
}

let current: ThemePreference = readPreference();

export function setThemePreference(preference: ThemePreference): void {
  current = preference;
  try {
    if (preference === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // Not saved, but still applied for this visit.
  }
  if (preference === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = preference;
  listeners.forEach((notify) => notify());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** React hook: re-renders the component when the theme changes. */
export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, () => current);
}
