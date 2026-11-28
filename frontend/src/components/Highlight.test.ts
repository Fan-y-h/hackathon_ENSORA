import { describe, expect, it } from "vitest";

import { findRanges, searchTerms } from "./Highlight";

describe("findRanges", () => {
  it("ignores accents and case", () => {
    expect(findRanges("Le Théorème central", ["theoreme"])).toEqual([[3, 11]]);
  });

  it("finds every occurrence and merges overlaps", () => {
    expect(findRanges("test test", ["test"])).toEqual([
      [0, 4],
      [5, 9],
    ]);
    expect(findRanges("intervalle", ["inter", "terval"])).toEqual([[0, 8]]);
  });

  it("keeps short words out of the search terms", () => {
    expect(searchTerms("loi de Student")).toEqual(["loi", "Student"]);
  });
});
