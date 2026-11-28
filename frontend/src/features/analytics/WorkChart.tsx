import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from "recharts";

import { useThemePreference } from "@/app/theme";
import { formatMinutes } from "@/lib/format";

/** Reads a CSS color token, again whenever the theme changes (SVG needs real colors). */
function useCssColor(name: string): string {
  const theme = useThemePreference();
  const [value, setValue] = useState("#0e7c7b");
  useEffect(() => {
    const read = () => setValue(getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#0e7c7b");
    read();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", read);
    return () => media.removeEventListener("change", read);
  }, [name, theme]);
  return value;
}

const dayLabel = (date: string, long = false) =>
  new Intl.DateTimeFormat("fr", long ? { weekday: "long", day: "numeric", month: "long" } : { day: "numeric", month: "numeric" }).format(new Date(`${date}T12:00:00`));

function ChartTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-sm">
      <p className="text-xs text-muted capitalize">{dayLabel(String(label), true)}</p>
      <p className="font-semibold tabular-nums">{formatMinutes(Number(payload[0].value))} de travail</p>
    </div>
  );
}

/** Minutes actually worked per day (single series: the title names it, no legend). */
export function WorkChart({ data }: { data: Array<{ date: string; minutes: number }> }) {
  const bar = useCssColor("--chart-bar");
  const grid = useCssColor("--chart-grid");
  const muted = useCssColor("--muted");

  return (
    <div className="h-56 w-full" role="img" aria-label={`Temps de travail par jour sur ${data.length} jours, total ${formatMinutes(data.reduce((s, d) => s + d.minutes, 0))}. Détail dans le tableau ci-dessous.`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }} barCategoryGap={data.length > 14 ? 2 : 6}>
          <CartesianGrid vertical={false} stroke={grid} />
          <XAxis dataKey="date" tickFormatter={(d: string) => dayLabel(d)} tick={{ fill: muted, fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={12} />
          <YAxis tick={{ fill: muted, fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} width={36} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: grid, opacity: 0.5 }} />
          <Bar dataKey="minutes" fill={bar} radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
