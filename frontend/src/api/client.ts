/**
 * Typed HTTP client for the Ensora+ API (`/api/v1`, docs/API_CONTRACTS.md).
 *
 * - The user is resolved server-side: no identity is ever sent in a body.
 * - Errors follow the `{error:{code,message,details,request_id}}` envelope and are
 *   surfaced as `ApiError` so screens can show distinct 409 / 422 / 403 / 503 states.
 * - No secrets live in the bundle: the base URL is a public value (VITE_API_BASE_URL).
 */

export const API_PREFIX = "/api/v1";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id?: string;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId?: string;

  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /** 409: the data changed since it was read (stale preview, revision mismatch). */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /** 403 exam_mode_active: feature disabled during an active exam attempt. */
  get isExamBlocked(): boolean {
    return this.status === 403 && this.code === "exam_mode_active";
  }

  /** 404/503: optional module or backend endpoint not yet mounted/running. */
  get isUnavailable(): boolean {
    return this.status === 404 || this.status === 503;
  }

  /** The backend could not be reached at all. */
  get isNetwork(): boolean {
    return this.status === 0;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  /** Multipart upload (documents): sent as-is, the browser sets the boundary header. */
  form?: FormData;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Sends an `Idempotency-Key` header (reviews, submissions). */
  idempotencyKey?: string;
}

export function buildUrl(path: string, query?: Query): string {
  const url = `${BASE_URL}${API_PREFIX}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ErrorEnvelope).error?.message === "string"
  );
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined && !options.form) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: options.form ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      credentials: "include",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "Le serveur Ensora+ est injoignable.");
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    if (isEnvelope(payload)) {
      const { code, message, details, request_id } = payload.error;
      throw new ApiError(response.status, code, message, details, request_id);
    }
    throw new ApiError(response.status, "http_error", `Erreur ${response.status}`);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>("GET", path, { query, signal }),
  post: <T>(path: string, body?: unknown, options: Omit<RequestOptions, "body"> = {}) =>
    request<T>("POST", path, { ...options, body: body ?? {} }),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, { body }),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, { body }),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, { form }),
  delete: <T>(path: string, query?: Query, body?: unknown) => request<T>("DELETE", path, { query, body }),
};

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** Human message for any thrown value, in French by default. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isNetwork) return error.message;
    if (error.isExamBlocked) return "Accès bloqué pendant un examen en cours.";
    if (error.status === 409) return "Les données ont changé entre-temps. Rechargez puis réessayez.";
    if (error.status === 422) return `Données invalides : ${error.message}`;
    if (error.status === 503) return "Service momentanément indisponible.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Erreur inconnue.";
}
