export type InternalRole = "admin" | "sales" | "customer";

const PUBLIC_ROUTES = ["/login", "/shop", "/legal", "/passwort-vergessen", "/zahlung"] as const;
const ADMIN_ROUTES = [
  "/produkte",
  "/benutzer",
  "/abos",
  "/audit",
  "/shop-admin",
  "/maschinen-admin",
  "/vertrieb",
  "/einstellungen",
  "/systembetrieb",
] as const;
const STAFF_ROUTES = ["/kunden", "/kunde", "/auswertungen", "/katalog"] as const;
const INTERNAL_ROUTES = [
  "/",
  "/angebote",
  "/bestellungen",
  "/bestellung",
  "/mehr",
  "/maschinen",
  "/passwort-aendern",
] as const;

function normalizePath(path: string): string {
  const withoutQuery = path.split(/[?#]/, 1)[0] || "/";
  const withoutRouteGroup = withoutQuery.replace(/^\/\(tabs\)(?=\/|$)/, "") || "/";
  return withoutRouteGroup.length > 1 ? withoutRouteGroup.replace(/\/+$/, "") : withoutRouteGroup;
}

function matches(path: string, route: string): boolean {
  return path === route || (route !== "/" && path.startsWith(`${route}/`));
}

export function isPublicRoute(path: string): boolean {
  const normalized = normalizePath(path);
  return PUBLIC_ROUTES.some((route) => matches(normalized, route));
}

export function canAccessRoute(path: string, role: InternalRole | null): boolean {
  const normalized = normalizePath(path);
  if (PUBLIC_ROUTES.some((route) => matches(normalized, route))) return true;
  if (!role) return false;
  if (ADMIN_ROUTES.some((route) => matches(normalized, route))) return role === "admin";
  if (STAFF_ROUTES.some((route) => matches(normalized, route))) {
    return role === "admin" || role === "sales";
  }
  if (INTERNAL_ROUTES.some((route) => matches(normalized, route))) return true;
  return false;
}

export function deniedRouteTarget(role: InternalRole | null): "/" | "/login" {
  return role ? "/" : "/login";
}
