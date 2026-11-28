// Low-level HTTP client: every request to the team backend goes through apiFetch.
// It adds the /api/v1 prefix, sends/reads JSON and turns error bodies into ApiError.
import { API_BASE_URL } from "@/app/config";

import type { ApiErrorBody } from "./types";

/** Error thrown for any non-2xx response, with the contract's error fields. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | undefined;

  constructor(status: number, body: ApiErrorBody["error"]) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details;
    this.requestId = body.request_id;
  }

  /** 409: someone (or another tab) changed the data first. Reload, then retry. */
  get isConflict(): boolean {
    return this.status === 409;
  }
}

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** Added to the URL; null/undefined values are skipped. */
  query?: Record<string, QueryValue>;
  /** Sent as JSON, or as multipart if it is a FormData (file upload). */
  body?: unknown;
  /** For POST that create jobs or evidence: a retry with the same key is not duplicated. */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  // The browser gives a real origin; tests run in Node where there is none.
  const origin = globalThis.location?.origin ?? "http://localhost";
  const url = new URL(`${API_BASE_URL}${path}`, origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === "object" && value !== null && "error" in value;
}

/** Sends the request and turns any non-2xx answer into an ApiError. */
async function send(path: string, options: RequestOptions, accept: string): Promise<Response> {
  const headers: Record<string, string> = { Accept: accept };
  // FormData (file upload): the browser sets the multipart Content-Type itself.
  const isForm = options.body instanceof FormData;
  if (options.body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : isForm ? (options.body as FormData) : JSON.stringify(options.body),
      credentials: "include", // session cookie: the user is never sent in the body
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, { code: "network_error", message: "Impossible de joindre le serveur. Vérifiez votre connexion." });
  }

  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    if (isErrorBody(data)) throw new ApiError(response.status, data.error);
    throw new ApiError(response.status, { code: "http_error", message: `Erreur inattendue du serveur (${response.status}).` });
  }
  return response;
}

/** JSON request. Pass a FormData as body to upload a file. */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options, "application/json");
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Raw file (PDF, text...), e.g. a document version's content. */
export async function apiFetchBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await send(path, options, "*/*");
  return response.blob();
}

/** New random key for one user action (keep it if you retry that same action). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
