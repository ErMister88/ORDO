import { storage } from "@/src/utils/storage";
import { API_BASE } from "@/src/api/client";

const KEY = "shop_token";

export async function shopToken(): Promise<string> {
  return (await storage.secureGet(KEY, "")) as string;
}
export async function shopSetToken(t: string) {
  await storage.secureSet(KEY, t);
}
export async function shopLogout() {
  await storage.secureRemove(KEY);
}

async function req(path: string, method: string, body?: any, auth = false): Promise<any> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth) {
    const t = await shopToken();
    if (t) headers.Authorization = `Bearer ${t}`;
  }
  const res = await fetch(`${API_BASE}/api${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
  return d;
}

export const shopApi = {
  register: (b: { name: string; email: string; password: string }) => req("/shop/register", "POST", b, false),
  login: (b: { email: string; password: string }) => req("/shop/login", "POST", b, false),
  me: () => req("/shop/me", "GET", undefined, true),
  myOrders: () => req("/shop/my-orders", "GET", undefined, true),
  createOrder: (b: any) => req("/shop/orders", "POST", b, true),
  newsletter: (b: { email: string; name?: string; baseUrl?: string }) => req("/newsletter/subscribe", "POST", b, false),
  validateCode: (code: string) => req("/shop/validate-code", "POST", { code }, false),
  saveAddress: (b: { name: string; phone: string; street: string; zip: string; city: string }) =>
    req("/shop/me/address", "PUT", b, true),
};
