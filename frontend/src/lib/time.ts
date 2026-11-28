export const DEFAULT_TZ = "Europe/Paris";
const DAY_MS = 86_400_000;
const clocks = new Map<string, Intl.DateTimeFormat>();

function parts(value: Date | string, timeZone: string) {
  let formatter = clocks.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    clocks.set(timeZone, formatter);
  }
  return Object.fromEntries(formatter.formatToParts(new Date(value)).map(({ type, value }) => [type, value]));
}

export function dayKey(value: Date | string, tz = DEFAULT_TZ): string {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const { year, month, day } = parts(value, tz);
  return `${year}-${month}-${day}`;
}

const calendarDate = (day: string) => new Date(`${day}T00:00:00Z`);

export function addDays(day: string, count: number): string {
  return new Date(calendarDate(day).getTime() + count * DAY_MS).toISOString().slice(0, 10);
}

export const weekdayOfKey = (day: string): number => (calendarDate(day).getUTCDay() + 6) % 7;
export const mondayOf = (day: string): string => addDays(day, -weekdayOfKey(day));
export const shortDay = (weekday: number): string =>
  ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."][weekday] ?? "";

/** Find the first instant of a civil day without assuming a fixed UTC offset. */
export function startOfLocalDay(day: string, tz = DEFAULT_TZ): Date {
  const nominal = calendarDate(day).getTime();
  let low = nominal - DAY_MS;
  let high = nominal + DAY_MS;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (dayKey(new Date(middle), tz) < day) low = middle;
    else high = middle;
  }
  return new Date(high);
}

/** The end date is exclusive. DST may make adjacent bounds 23 or 25 hours apart. */
export function dayRange(start: string, end: string, tz = DEFAULT_TZ) {
  return { start: startOfLocalDay(start, tz).toISOString(), end: startOfLocalDay(end, tz).toISOString() };
}

/** Convert a local day (`YYYY-MM-DD`) and time (`HH:MM`) in `tz` to a UTC ISO string. */
export function localDateTimeToIso(day: string, hhmm: string, tz = DEFAULT_TZ): string {
  const [h, m] = hhmm.split(":").map(Number);
  const targetMin = (h || 0) * 60 + (m || 0);
  const start = startOfLocalDay(day, tz).getTime();
  // Binary search around start + targetMin to handle DST transitions cleanly.
  let approx = start + targetMin * 60_000;
  for (let i = 0; i < 4; i += 1) {
    const d = new Date(approx);
    const dk = dayKey(d, tz);
    const actualMin = minutesOfDay(d, tz);
    let diffMin = targetMin - actualMin;
    if (dk < day) diffMin += 24 * 60;
    else if (dk > day) diffMin -= 24 * 60;
    if (diffMin === 0) break;
    approx += diffMin * 60_000;
  }
  return new Date(approx).toISOString();
}

export function minutesOfDay(value: Date | string, tz = DEFAULT_TZ): number {
  const { hour, minute } = parts(value, tz);
  return Number(hour) * 60 + Number(minute);
}

export function formatClock(value: Date | string, tz = DEFAULT_TZ): string {
  const { hour, minute } = parts(value, tz);
  return `${hour} h ${minute}`;
}

export function formatTimeInput(value: Date | string, tz = DEFAULT_TZ): string {
  const { hour, minute } = parts(value, tz);
  return `${hour}:${minute}`;
}

export function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes)) return "—";
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const remainder = total % 60;
  if (!hours) return `${total} min`;
  return remainder ? `${hours} h ${String(remainder).padStart(2, "0")}` : `${hours} h`;
}

const longDate = new Intl.DateTimeFormat("fr-FR", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" });
const shortDate = new Intl.DateTimeFormat("fr-FR", { timeZone: "UTC", day: "numeric", month: "short" });
const percent = new Intl.NumberFormat("fr-FR", { style: "percent", maximumFractionDigits: 0 });

export const formatLongDate = (day: string): string => longDate.format(calendarDate(day));
export const formatShortDate = (day: string): string => shortDate.format(calendarDate(day));
export const formatPercent = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "—" : percent.format(value);

export function countdownLabel(deadline: string, now: Date, tz = DEFAULT_TZ): string {
  const days = (calendarDate(dayKey(deadline, tz)).getTime() - calendarDate(dayKey(now, tz)).getTime()) / DAY_MS;
  return days === 0 ? "Aujourd'hui" : days > 0 ? `J‑${days}` : `J+${-days}`;
}
