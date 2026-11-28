import { useId, type ComponentProps, type ReactNode } from "react";

import { cn } from "@/lib/cn";

interface FieldShellProps {
  id: string;
  label: string;
  hint?: ReactNode;
  /** Validation message shown under the field (e.g. a 422 detail from the API). */
  error?: string;
  required?: boolean;
  children: ReactNode;
}

function FieldShell({ id, label, hint, error, required, children }: FieldShellProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
        {required && (
          <span className="text-danger" aria-hidden="true">
            {" "}
            *
          </span>
        )}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-xs font-medium text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

const CONTROL = cn(
  "focus-ring h-10 w-full rounded-lg border bg-surface px-3 text-sm text-ink",
  "placeholder:text-muted disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted",
);

/** Links the input to its hint/error so screen readers read them. */
function describedBy(id: string, hint?: ReactNode, error?: string): string | undefined {
  if (error) return `${id}-error`;
  if (hint) return `${id}-hint`;
  return undefined;
}

type TextFieldProps = Omit<ComponentProps<"input">, "id"> & {
  label: string;
  hint?: ReactNode;
  error?: string;
};

export function TextField({ label, hint, error, required, className, ...props }: TextFieldProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} hint={hint} error={error} required={required}>
      <input
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        className={cn(CONTROL, error ? "border-danger" : "border-line", className)}
        {...props}
      />
    </FieldShell>
  );
}

type SelectFieldProps = Omit<ComponentProps<"select">, "id"> & {
  label: string;
  hint?: ReactNode;
  error?: string;
  options: Array<{ value: string; label: string }>;
};

export function SelectField({ label, hint, error, required, options, className, ...props }: SelectFieldProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} hint={hint} error={error} required={required}>
      <select
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        className={cn(CONTROL, error ? "border-danger" : "border-line", className)}
        {...props}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}
