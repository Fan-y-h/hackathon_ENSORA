import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, buildUrl, errorMessage } from "../src/api/client";

afterEach(() => vi.restoreAllMocks());

const respond = (status: number, body: unknown) =>
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body === null ? null : JSON.stringify(body), { status }));

describe("api client", () => {
  it("builds /api/v1 URLs and drops empty query values", () => {
    expect(buildUrl("/events", { start: "a", end: "b", type: undefined, limit: 50 })).toBe("/api/v1/events?start=a&end=b&limit=50");
  });

  it("parses the error envelope into ApiError", async () => {
    respond(409, { error: { code: "conflict", message: "stale preview", details: null, request_id: "r1" } });
    const err = (await api.post("/planner/accept", {}).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.isConflict).toBe(true);
    expect(err.requestId).toBe("r1");
    expect(errorMessage(err)).toMatch(/changé/);
  });

  it("sends the Idempotency-Key header and JSON body", async () => {
    const spy = respond(200, { ok: true });
    await api.post("/cards/1/review", { rating: "good" }, { idempotencyKey: "k-1" });
    const init = spy.mock.calls[0][1]!;
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("k-1");
    expect(init.body).toBe(JSON.stringify({ rating: "good" }));
  });

  it("reports an unreachable backend as a network error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = (await api.get("/health").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.isNetwork).toBe(true);
  });

  it("returns undefined on 204", async () => {
    respond(204, null);
    await expect(api.delete("/tasks/1")).resolves.toBeUndefined();
  });
});
