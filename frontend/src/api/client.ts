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
