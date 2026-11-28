import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../src/api/client";
import { SyncButton } from "../src/features/sources/SyncButton";

afterEach(() => vi.restoreAllMocks());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("sources client", () => {
  it("uploads multipart without forcing a JSON content type", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ result: "documents" }, 201));
    const form = new FormData();
    form.append("file", new Blob(["x"], { type: "text/plain" }), "cours.txt");
    await api.upload("/sources/documents/import", form);
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/v1/sources/documents/import");
    expect(init!.body).toBe(form);
    expect((init!.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });
});

describe("SyncButton", () => {
  it("starts a sync, follows the job, then copies events into the agenda", async () => {
    const calls: string[] = [];
    let polls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/sources/sync")) return json({ id: "job-1" }, 202);
      if (url.endsWith("/sources/jobs/job-1")) {
        polls += 1;
        return json({ id: "job-1", status: polls < 2 ? "running" : "succeeded", started_at: "", finished_at: null, report: {} });
      }
      if (url.endsWith("/agenda/import-from-sources")) {
        return json({
          sources: [
            { source: "pamplemousse", agenda_source: "pamplemousse", read: 3, skipped: 0, result: { created: 3, updated: 0, unchanged: 0, agenda_revision: 4 } },
          ],
        });
      }
      return json({}, 404);
    });

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <SyncButton />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: /synchroniser/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("3 événement(s) mis à jour"), { timeout: 6000 });
    expect(calls).toContain("POST /api/v1/sources/sync");
    expect(calls.filter((c) => c === "POST /api/v1/agenda/import-from-sources")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /synchroniser/i })).toBeEnabled();
  }, 10000);
});
