/** Student category (élève fonctionnaire / ingénieur·e). Options and labels come from the API. */
import { errorMessage } from "../../api/client";
import { useProfile, useStudentStatuses, useUpdateProfile } from "../../api/queries";
import type { StudentStatus } from "../../api/types";
import { Icon } from "../../components/Icon";
import "./profile.css";

export function useStudentStatusLabel(): string | null {
  const profile = useProfile();
  const options = useStudentStatuses();
  const status = profile.data?.student_status;
  return status ? (options.data?.find((o) => o.status === status)?.label ?? null) : null;
}

export function StudentStatusPicker() {
  const profile = useProfile();
  const options = useStudentStatuses();
  const update = useUpdateProfile();
  const current = profile.data?.student_status ?? null;

  if (!profile.data || !options.data) {
    return <div className="skeleton" style={{ minHeight: 56 }} aria-busy="true" />;
  }

  const choose = (status: StudentStatus) => {
    if (status === current || update.isPending) return;
    update.mutate({ expected_revision: profile.data!.revision, student_status: status });
  };

  return (
    <div className="stack">
      <div className="status-choice" role="radiogroup" aria-label="Catégorie d'élève">
        {options.data.map((o) => {
          const selected = o.status === current;
          return (
            <button
              key={o.status}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={update.isPending}
              className={`status-choice__option${selected ? " status-choice__option--selected" : ""}`}
              onClick={() => choose(o.status)}
            >
              <span className="status-choice__head">
                <span className="status-choice__radio" aria-hidden="true">{selected && <Icon name="check" size={14} />}</span>
                <strong>{o.label}</strong>
              </span>
            </button>
          );
        })}
      </div>
      {update.error && <div className="status status--critical">{errorMessage(update.error)}</div>}
    </div>
  );
}
