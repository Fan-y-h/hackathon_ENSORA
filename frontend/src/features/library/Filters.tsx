import type { DocumentSummary, ResourceType, Subject } from "@/api/types";
import { SelectField } from "@/components/ui";
import { RESOURCE_TYPE_LABELS, RESOURCE_TYPE_ORDER } from "@/lib/labels";

interface FiltersProps {
  subjects: Subject[];
  /** All documents, to offer only the years that exist. */
  documents: DocumentSummary[];
  subjectId?: string;
  type?: ResourceType;
  year?: number;
  onChange: (changes: { subject?: string; type?: string; year?: string }) => void;
}

export function Filters({ subjects, documents, subjectId, type, year, onChange }: FiltersProps) {
  const years = [...new Set(documents.map((d) => d.year).filter((y): y is number => y !== null))].sort((a, b) => b - a);

  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <SelectField
        label="Matière"
        value={subjectId ?? ""}
        onChange={(e) => onChange({ subject: e.target.value })}
        options={[{ value: "", label: "Toutes les matières" }, ...subjects.map((s) => ({ value: s.id, label: s.title }))]}
      />
      <SelectField
        label="Type"
        value={type ?? ""}
        onChange={(e) => onChange({ type: e.target.value })}
        options={[{ value: "", label: "Tous les types" }, ...RESOURCE_TYPE_ORDER.map((t) => ({ value: t, label: RESOURCE_TYPE_LABELS[t] }))]}
      />
      <SelectField
        label="Année (annales)"
        value={year ? String(year) : ""}
        onChange={(e) => onChange({ year: e.target.value })}
        options={[{ value: "", label: "Toutes les années" }, ...years.map((y) => ({ value: String(y), label: String(y) }))]}
      />
    </div>
  );
}
