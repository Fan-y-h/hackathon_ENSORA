export const STANDARD_PALETTE = [
  { key: "bleu", name: "Bleu", hex: "#0072B2", accessible: "#004488", dark: "#77BBEE" },
  { key: "vermillon", name: "Vermillon", hex: "#D55E00", accessible: "#992F17", dark: "#FFA177" },
  { key: "vert", name: "Vert", hex: "#009E73", accessible: "#00634C", dark: "#66DDBB" },
  { key: "rose", name: "Rose", hex: "#C2629A", accessible: "#AA3377", dark: "#F0A3CF" },
  { key: "prune", name: "Prune", hex: "#5B3FA8", accessible: "#702F70", dark: "#B79EF5" },
  { key: "moutarde", name: "Moutarde", hex: "#A87A00", accessible: "#994400", dark: "#F0C850" },
  { key: "brun", name: "Brun", hex: "#8C5A2B", accessible: "#734021", dark: "#E8B787" },
  { key: "petrole", name: "Pétrole", hex: "#1F6F7A", accessible: "#115566", dark: "#6FD0DD" },
  { key: "framboise", name: "Framboise", hex: "#B8336A", accessible: "#882255", dark: "#F28CB3" },
  { key: "olive", name: "Olive", hex: "#6B7F1E", accessible: "#556611", dark: "#B5CC5E" },
] as const;

export type PaletteKey = (typeof STANDARD_PALETTE)[number]["key"];

export function colorByKey(key: string) {
  return STANDARD_PALETTE.find((color) => color.key === key) ?? STANDARD_PALETTE[0];
}

const confusablePairs: readonly [string, string][] = [
  ["bleu", "prune"],
  ["vermillon", "brun"],
  ["vermillon", "olive"],
  ["vert", "olive"],
  ["rose", "framboise"],
  ["moutarde", "brun"],
  ["moutarde", "olive"],
  ["orange", "brun"],
  ["rouge", "brun"],
];

export function confusableWith(key: string, others: string[]): string | null {
  return (
    others.find(
      (other) =>
        other === key ||
        confusablePairs.some(([a, b]) => (a === key && b === other) || (b === key && a === other)),
    ) ?? null
  );
}

interface ColorPreferences {
  overrides: Record<string, string>;
  accessible: boolean;
  dark: boolean;
}

export function resolveSubjectColorKey(id: string, ids: string[], overrides: Record<string, string> = {}): PaletteKey {
  const override = STANDARD_PALETTE.find((entry) => entry.key === overrides[id]);
  if (override) return override.key;
  const position = [...new Set([...ids, id])].sort().indexOf(id);
  return STANDARD_PALETTE[position % STANDARD_PALETTE.length].key;
}

/** Keep palette assignments independent of the order in which queries return subjects. */
export function resolveSubjectColor(id: string, ids: string[], preferences: ColorPreferences): string {
  const color = colorByKey(resolveSubjectColorKey(id, ids, preferences.overrides));
  return preferences.dark ? color.dark : preferences.accessible ? color.accessible : color.hex;
}

export function subjectAcronym(id: string | null | undefined): string {
  return id?.trim().split(/[-_\s]+/)[0]?.slice(0, 4).toLocaleUpperCase("fr") || "—";
}

export function deriveSubjectNames(events: { subject_id?: string | null; title: string; type?: string }[]): Record<string, string> {
  const names: Record<string, string> = Object.create(null);
  for (const event of events) {
    if (!event.subject_id || names[event.subject_id] || !["class", "td", "tp"].includes(event.type ?? "")) continue;
    const title = event.title.replace(/^(CM|TD|TP)\s*[:–-]\s*/i, "").trim();
    if (title) names[event.subject_id] = title;
  }
  return names;
}
