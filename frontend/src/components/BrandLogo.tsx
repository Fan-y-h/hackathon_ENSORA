import logoUrl from "../assets/ensora-logo.jpg";
import "./brand-logo.css";

/**
 * Ensora club logo (pandas + "ENSORA") with the Ensora+ coral "+".
 * The source JPG has a white background and wide margins: it sits in a white
 * rounded badge (readable on the navy sidebar in light AND dark mode) and is
 * cropped by CSS to the drawing itself, so the original file stays untouched.
 */
export function BrandLogo({ size = "md" }: { size?: "sm" | "md" }) {
  return (
    <div className={`brand-logo brand-logo--${size}`}>
      <div className="brand-logo__crop">
        <img src={logoUrl} alt="Ensora+, club de tutorat de l'ENSAI" width={738} height={414} decoding="async" />
      </div>
      <span className="brand-logo__plus" aria-hidden="true">
        +
      </span>
    </div>
  );
}
