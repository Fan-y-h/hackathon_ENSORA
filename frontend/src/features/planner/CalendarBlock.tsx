import { usePreferences } from "../../app/preferences";
import { useSubjects } from "../../app/subjects";
import { Icon, type IconName } from "../../components/Icon";
import { SubjectBadge } from "../../components/ui";
import { formatClock, formatDuration } from "../../lib/time";
import { KIND_LABEL, type Block, type BlockKind } from "./calendar";

export const KIND_ICON: Record<BlockKind, IconName> = {
  course: "book",
  revision: "repeat",
  task: "checkSquare",
  exam: "clock",
  personal: "sun",
};

export function CalendarBlock({
  block,
  top,
  height,
  onOpen,
  detailed = false,
}: {
  block: Block;
  top: number;
  height: number;
  onOpen: (block: Block) => void;
  detailed?: boolean;
}) {
  const subjects = useSubjects();
  const { alwaysShowAcronyms } = usePreferences();
  const color = subjects.color(block.subjectId);
  const width = 100 / block.lanes;
  const time = `${formatClock(block.startAt, subjects.timezone)} – ${formatClock(block.endAt, subjects.timezone)}`;
  const duration = formatDuration(block.endMin - block.startMin);
  const status = block.session?.status;
  const done = status === "completed" || status === "partial" || status === "skipped";

  return (
    <button
      type="button"
      className={`cal-block cal-block--${block.kind}${done ? " cal-block--done" : ""}`}
      style={{
        top,
        height: Math.max(height, 22),
        left: `calc(${block.lane * width}% + 2px)`,
        width: `calc(${width}% - 4px)`,
        borderLeftColor: color ?? undefined,
      }}
      onClick={() => onOpen(block)}
      aria-label={`${KIND_LABEL[block.kind]} : ${block.title}, ${time}${block.locked ? ", verrouillé" : ""}`}
    >
      <span className="cal-block__head">
        {/* Short blocks in week view keep the shape/fill cue and drop the icon to leave room for the title. */}
        {(detailed || height >= 34) && <Icon name={KIND_ICON[block.kind]} size={13} />}
        <span className="cal-block__title">{block.title}</span>
        {block.locked && block.origin === "planner" && <Icon name="lock" size={12} />}
        {(alwaysShowAcronyms || height >= 34) && <SubjectBadge acronym={subjects.acronym(block.subjectId)} color={color} />}
      </span>
      {height >= 34 && (
        <span className="cal-block__meta">
          <strong className="tabular">{time}</strong>
          {detailed && <> · {duration}</>} · {KIND_LABEL[block.kind]}
          {block.subtitle && ` · ${block.subtitle}`}
          {done && ` · ${status === "completed" ? "terminée" : status === "partial" ? "partielle" : "non faite"}`}
        </span>
      )}
    </button>
  );
}
