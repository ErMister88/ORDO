import { storage } from "@/src/utils/storage";

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
if (!API) {
  throw new Error(
    "EXPO_PUBLIC_BACKEND_URL fehlt. Bitte die öffentliche Backend-Adresse ohne /api konfigurieren.",
  );
}
export const TOKEN_KEY = "ss_auth_token";
let authFailureHandler: (() => void | Promise<void>) | null = null;

export function setAuthFailureHandler(handler: (() => void | Promise<void>) | null) {
  authFailureHandler = handler;
}

async function handleAuthFailure(res: Response, requestToken: string) {
  if (res.status !== 401 || !requestToken) return;
  const currentToken = await storage.secureGet<string>(TOKEN_KEY, "");
  // A delayed response from the previous user must never clear a newer login.
  if (currentToken !== requestToken) return;
  await storage.secureRemove(TOKEN_KEY);
  await authFailureHandler?.();
}

export type Role = "admin" | "sales" | "customer";
export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  companyId?: string | null;
  salesRepId?: string | null;
  must_change_password?: boolean;
};

async function authContext(): Promise<{
  headers: Record<string, string>;
  token: string;
}> {
  const token = (await storage.secureGet<string>(TOKEN_KEY, "")) ?? "";
  return {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    token,
  };
}

export async function apiGet<T = any>(path: string, extraHeaders: Record<string, string> = {}): Promise<T> {
  const { headers, token } = await authContext();
  const res = await fetch(`${API}/api${path}`, { headers: { ...headers, ...extraHeaders } });
  await handleAuthFailure(res, token);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
  return res.json();
}

export async function apiPost<T = any>(path: string, body?: any, extraHeaders: Record<string, string> = {}): Promise<T> {
  const { headers, token } = await authContext();
  const res = await fetch(`${API}/api${path}`, {
    method: "POST",
    headers: { ...headers, ...extraHeaders, "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  await handleAuthFailure(res, token);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
  return res.json();
}

const pendingIdempotencyKeys = new Map<string, string>();
const PENDING_IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;

function stableLocalHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

type StoredIdempotencyIntent = { key: string; createdAt: number };

function canonicalRequestValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalRequestValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, nested]) => nested !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, nested]) => [key, canonicalRequestValue(nested)]),
    );
  }
  return value;
}

async function readPersistedIdempotencyKey(storageKey: string): Promise<string | null> {
  const raw = await storage.getItem<string>(storageKey, "");
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as StoredIdempotencyIntent;
    if (
      typeof value.key === "string"
      && typeof value.createdAt === "number"
      && value.createdAt <= Date.now()
      && Date.now() - value.createdAt <= PENDING_IDEMPOTENCY_TTL_MS
    ) {
      return value.key;
    }
  } catch {
    // Invalid local state is discarded and can never influence the server hash.
  }
  await storage.removeItem(storageKey);
  return null;
}

function newIdempotencyKey(): string {
  const randomUUID = globalThis.crypto?.randomUUID?.bind(globalThis.crypto);
  return randomUUID
    ? `ordo-${randomUUID()}`
    : `ordo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

async function apiIdempotent<T>(
  method: "POST" | "PUT",
  path: string,
  body?: any,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const serialized = JSON.stringify(canonicalRequestValue(body ?? {}));
  const intent = `${method}:${path}:${serialized}`;
  const { headers, token } = await authContext();
  const authorizationScope = extraHeaders.Authorization ?? headers.Authorization ?? "guest";
  const scopedIntent = `${stableLocalHash(authorizationScope)}:${intent}`;
  const storageKey = `ordo_pending_idempotency_${stableLocalHash(scopedIntent)}`;
  const key = pendingIdempotencyKeys.get(scopedIntent)
    ?? await readPersistedIdempotencyKey(storageKey)
    ?? newIdempotencyKey();
  pendingIdempotencyKeys.set(scopedIntent, key);
  const persisted = await storage.setItem(storageKey, JSON.stringify({ key, createdAt: Date.now() }));
  if (!persisted) {
    pendingIdempotencyKeys.delete(scopedIntent);
    throw new Error("Der Vorgang konnte nicht sicher vorbereitet werden. Bitte lokalen Speicher freigeben und erneut versuchen.");
  }
  try {
    const res = await fetch(`${API}/api${path}`, {
      method,
      headers: {
        ...headers,
        ...extraHeaders,
        "Content-Type": "application/json",
        "Idempotency-Key": key,
      },
      body: serialized,
    });
    await handleAuthFailure(res, token);
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const retryable = res.status >= 500 || (res.status === 409 && res.headers.has("Retry-After"));
      if (!retryable) {
        pendingIdempotencyKeys.delete(scopedIntent);
        await storage.removeItem(storageKey);
      }
      throw new Error(payload.detail || `Fehler ${res.status}`);
    }
    pendingIdempotencyKeys.delete(scopedIntent);
    await storage.removeItem(storageKey);
    return payload as T;
  } catch (error) {
    // Keep the key on transport failure so a user retry recovers the same server operation.
    throw error;
  }
}

export function apiPostIdempotent<T = any>(
  path: string,
  body?: any,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  return apiIdempotent<T>("POST", path, body, extraHeaders);
}

export function apiPutIdempotent<T = any>(path: string, body?: any): Promise<T> {
  return apiIdempotent<T>("PUT", path, body);
}

export async function apiPut<T = any>(path: string, body?: any): Promise<T> {
  const { headers, token } = await authContext();
  const res = await fetch(`${API}/api${path}`, {
    method: "PUT",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  await handleAuthFailure(res, token);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
  return res.json();
}

export const API_BASE = API;

export async function apiDelete<T = any>(path: string): Promise<T> {
  const { headers, token } = await authContext();
  const res = await fetch(`${API}/api${path}`, { method: "DELETE", headers });
  await handleAuthFailure(res, token);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
  return res.json();
}

export function fileUrl(url?: string | null): string | undefined {
  if (!url) return undefined;
  if (url.startsWith("http")) return url;
  return `${API}${url}`;
}

export async function apiUpload(
  uri: string,
  name: string,
  type: string,
): Promise<{ url: string; path: string }> {
  const { Platform } = require("react-native");
  const { headers, token } = await authContext();
  const form = new FormData();
  if (Platform.OS === "web") {
    const blob = await (await fetch(uri)).blob();
    form.append("file", blob, name);
  } else {
    form.append("file", { uri, name, type } as any);
  }
  const res = await fetch(`${API}/api/upload`, { method: "POST", headers, body: form });
  await handleAuthFailure(res, token);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Upload fehlgeschlagen (${res.status})`);
  return res.json();
}

export async function loginRequest(email: string, password: string): Promise<{ access_token: string; user: User }> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "E-Mail oder Passwort falsch");
  return res.json();
}

export async function fetchMe(): Promise<User> {
  return apiGet<User>("/auth/me");
}
