import { cn } from "@/lib/cn";

/**
 * Official ENSAI logo, downloaded unchanged from https://ensai.fr/
 * (public/brand/ensai-logo.png, 140x75). Never redraw, recolor or crop it.
 * The file has dark text, so it always sits on a white plate to stay readable
 * on the dark sidebar and in dark mode.
 */
export function EnsaiLogo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex rounded-md bg-white p-1.5", className)}>
      <img src="/brand/ensai-logo.png" alt="ENSAI" width={140} height={75} className="h-auto w-full" />
    </span>
  );
}
