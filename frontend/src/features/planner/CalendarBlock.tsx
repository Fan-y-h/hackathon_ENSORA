import { useSubjects } from "../../app/subjects";
import { Icon, type IconName } from "../../components/Icon";
import { SubjectBadge } from "../../components/ui";
import { formatClock, formatDuration } from "../../lib/time";
import { KIND_LABEL, ORIGIN_LABEL, type Block, type BlockKind } from "./calendar";

export const KIND_ICON: Record<BlockKind, IconName> = {
  course: "book",
  revision: "repeat",
  task: "checkSquare",
  exam: "clock",
  deadline: "flag",
  personal: "user",
};

type State = "planned" | "now" | "done" | "missed";

function stateOf(block: Block, isNext: boolean): State {
  const s = block.session?.status;
  if (s === "completed" || s === "partial") return "done";
  if (s === "skipped") return "missed";
  if (s === "in_progress" || isNext) return "now";
  return "planned";
}

/**
 * One calendar item. Type = shape + icon + word; subject = left stripe + acronym;
 * "movable" = grip ⋮⋮ (planner items), lock when locked; "now" = coral ring;
 * "conflict" = explicit warning badge & outline (never masked by side-by-side lanes).
 */
export function CalendarBlock({
  block,
  top,
  height,
  onOpen,
  isNext = false,
}: {
  block: Block;
  top: number;
  height: number;
  onOpen: (block: Block) => void;
  isNext?: boolean;
}) {
  const subjects = useSubjects();
  const tz = subjects.timezone;
  const color = subjects.color(block.subjectId);
  const acronym = subjects.acronym(block.subjectId);
  const time = `${formatClock(block.startAt, tz)} – ${formatClock(block.endAt, tz)}`;

  if (block.kind === "deadline") {
    return (
      <button
        type="button"
        className="cal-marker"
        style={{ top }}
        onClick={() => onOpen(block)}
        aria-label={`Échéance (${ORIGIN_LABEL[block.sourceOrigin]}) : ${block.title}, ${formatClock(block.startAt, tz)}`}
      >
        <span className="cal-marker__flag" style={{ borderColor: color }}>
          <Icon name="flag" size={12} />
          <span className="cal-marker__text">{block.title}</span>
          <span className="tabular">{formatClock(block.startAt, tz)}</span>
        </span>
      </button>
    );
  }

  const state = stateOf(block, isNext);
  const width = 100 / block.lanes;
  const roomy = height >= 40;
  const movable = block.origin === "planner";
  const compact = height < 36;

  return (
    <button
      type="button"
      className={`cal-block cal-block--${block.kind}${block.tentative ? " cal-block--tentative" : ""}${
        block.conflict ? " cal-block--conflict" : ""
      } cal-block--${state}`}
      style={{
        top,
        height: Math.max(height, 22),
        left: `calc(${block.lane * width}% + 2px)`,
        width: `calc(${width}% - 4px)`,
        borderLeftColor: block.kind === "personal" ? undefined : color,
      }}
      onClick={() => onOpen(block)}
      aria-label={`${KIND_LABEL[block.kind]} (${ORIGIN_LABEL[block.sourceOrigin]})${block.tentative ? " à confirmer" : ""}${
        block.conflict ? ", en conflit horaire" : ""
      } : ${block.title}, ${time}${block.locked && movable ? ", verrouillée" : ""}${
        state === "done" ? ", faite" : state === "missed" ? ", non faite" : state === "now" ? ", maintenant" : ""
      }`}
    >
      <span className="cal-block__tagline">
        {block.conflict ? (
          <Icon name="warning" size={12} label="Conflit horaire" />
        ) : state === "done" ? (
          <Icon name="check" size={12} strokeWidth={3} />
        ) : (
          <Icon name={KIND_ICON[block.kind]} size={12} />
        )}
        <span className={compact ? "cal-block__tag cal-block__tag--short" : "cal-block__tag"}>
          {block.conflict && !compact
            ? `CONFLIT · ${block.tag}`
            : block.tentative && !compact
              ? `${block.tag} · À CONFIRMER`
              : block.tag}
        </span>
        {compact && <span className="cal-block__title cal-block__title--inline">{block.title}</span>}
        {acronym && acronym !== "—" && <SubjectBadge acronym={acronym} color={color} />}
        {movable && (block.locked ? <Icon name="lock" size={12} /> : <span className="cal-block__grip" aria-hidden="true">⋮⋮</span>)}
      </span>
      {!compact && <span className="cal-block__title">{block.title}</span>}
      {roomy && (
        <span className="cal-block__meta tabular">
          {state === "missed" ? "Non faite" : time}
          {height >= 70 && ` · ${formatDuration(block.endMin - block.startMin)}`}
          {block.subtitle && ` · ${block.subtitle}`}
        </span>
      )}
    </button>
  );
}
