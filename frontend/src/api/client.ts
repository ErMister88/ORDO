import { storage } from "@/src/utils/storage";

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
export const TOKEN_KEY = "ss_auth_token";

export type Role = "admin" | "sales" | "customer";
export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  companyId?: string | null;
  salesRepId?: string | null;
};

async function authHeader(): Promise<Record<string, string>> {
  const token = await storage.secureGet<string>(TOKEN_KEY, "");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function apiGet<T = any>(path: string): Promise<T> {
  const headers = await authHeader();
  const res = await fetch(`${API}/api${path}`, { headers });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
  return res.json();
}

export async function apiPost<T = any>(path: string, body?: any): Promise<T> {
  const headers = await authHeader();
  const res = await fetch(`${API}/api${path}`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Fehler ${res.status}`);
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
