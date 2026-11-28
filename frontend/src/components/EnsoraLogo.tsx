import { cn } from "@/lib/cn";

/**
 * Official ENSORA club logo (public/brand/ensora-logo.svg).
 * Vectorized automatically from the club's image, colors snapped to the original
 * ones (#000000, #99B5CB, #E58687, #FFFFFF). Never redraw or recolor it.
 * The text is black, so the logo always sits on a white plate to stay readable
 * on the dark sidebar and in dark mode.
 */
export function EnsoraLogo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex rounded-lg bg-white p-2", className)}>
      <img src="/brand/ensora-logo.svg" alt="ENSORA" width={1268} height={824} className="h-auto w-full" />
    </span>
  );
}
