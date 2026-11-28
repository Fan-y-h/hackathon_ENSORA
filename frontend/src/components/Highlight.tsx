import type { ReactNode } from "react";

/** Lowercase, accents removed, one output char per input char (so indexes still match). */
function foldChar(char: string): string {
  const folded = char.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  return folded.length === 1 ? folded : char.toLowerCase();
}

/** [start, end) ranges of every term found in text, accent- and case-insensitive. */
export function findRanges(text: string, terms: string[]): Array<[number, number]> {
  const folded = Array.from(text, foldChar).join("");
  const ranges: Array<[number, number]> = [];
  for (const term of terms) {
    const needle = Array.from(term, foldChar).join("");
    if (needle.length < 2) continue;
    let index = folded.indexOf(needle);
    while (index !== -1) {
      ranges.push([index, index + needle.length]);
      index = folded.indexOf(needle, index + needle.length);
    }
  }
  // Merge overlapping ranges.
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const range of ranges) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

/** Search words typed by the student, ignoring very short ones ("de", "la"...). */
export function searchTerms(query: string): string[] {
  return query.split(/\s+/).filter((word) => word.length >= 3);
}

interface HighlightProps {
  text: string;
  terms: string[];
}

/** Renders text with every search term wrapped in <mark>. */
export function Highlight({ text, terms }: HighlightProps) {
  const ranges = findRanges(text, terms);
  if (ranges.length === 0) return <>{text}</>;
  const parts: ReactNode[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], i) => {
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <mark key={i} className="rounded-sm bg-warning-soft px-0.5 text-ink">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}
