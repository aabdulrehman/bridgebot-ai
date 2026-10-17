/**
 * Tiny typed API client.
 *
 * API base = NEXT_PUBLIC_API_URL (e.g. https://bridgebot-api.onrender.com).
 * When empty, the current origin is used (frontend served by the backend).
 */
const TOKEN_KEY = "bridgebot_token";

export function apiBase(): string {
  const env = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/+$/, "");
  if (env) return env;
  if (typeof window !== "undefined") return window.location.origin;
  return "http://localhost:8000";
}

export function wsBase(): string {
  return apiBase().replace(/^http/, "ws");
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** fetch wrapper: JSON in/out, bearer auth, readable errors, auto-logout on 401. */
export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, { ...init, headers, body });
  } catch {
    throw new ApiError(0, `Cannot reach API at ${apiBase()} (server asleep or offline?)`);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
      setToken(null);
      window.location.href = "/login/";
    }
    const detail = (data as { detail?: unknown }).detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, msg || `HTTP ${res.status}`);
  }
  return data as T;
}
