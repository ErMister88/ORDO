import assert from "node:assert/strict";
import test from "node:test";

import { canAccessRoute, deniedRouteTarget, isPublicRoute } from "../src/auth/route-access.ts";

test("admin may open all internal route classes directly", () => {
  for (const path of [
    "/",
    "/produkte", "/benutzer", "/abos", "/audit", "/shop-admin", "/maschinen-admin",
    "/kunden", "/kunde/c1", "/auswertungen", "/angebote", "/bestellungen",
    "/bestellung/B-1", "/mehr", "/maschinen", "/passwort-aendern",
  ]) assert.equal(canAccessRoute(path, "admin"), true, path);
});

test("sales direct URLs allow staff workflows and reject admin-only routes", () => {
  for (const path of ["/kunden", "/kunde/c1", "/angebote", "/bestellungen", "/auswertungen", "/maschinen"])
    assert.equal(canAccessRoute(path, "sales"), true, path);
  for (const path of ["/benutzer", "/produkte", "/abos", "/audit", "/shop-admin", "/maschinen-admin"])
    assert.equal(canAccessRoute(path, "sales"), false, path);
});

test("B2B customer direct URLs allow only customer workflows", () => {
  for (const path of ["/angebote", "/bestellungen", "/bestellung/B-1", "/mehr", "/maschinen"])
    assert.equal(canAccessRoute(path, "customer"), true, path);
  for (const path of ["/produkte", "/benutzer", "/kunden", "/kunde/c2", "/auswertungen", "/shop-admin"])
    assert.equal(canAccessRoute(path, "customer"), false, path);
});

test("B2C and anonymous users cannot enter internal routes", () => {
  for (const path of ["/shop", "/shop/warenkorb", "/shop/konto", "/legal/impressum", "/login"])
    assert.equal(canAccessRoute(path, null), true, path);
  for (const path of ["/produkte", "/benutzer", "/kunden", "/angebote", "/bestellungen", "/maschinen"])
    assert.equal(canAccessRoute(path, null), false, path);
  assert.equal(canAccessRoute("/", null), false);
});

test("route groups, query strings and unknown routes cannot bypass the matrix", () => {
  assert.equal(canAccessRoute("/(tabs)/kunden?tenantId=other", "customer"), false);
  assert.equal(canAccessRoute("/(tabs)/angebote?tenantId=other", "customer"), true);
  assert.equal(canAccessRoute("/benutzer/../produkte", "sales"), false);
  assert.equal(canAccessRoute("/not-a-real-internal-route", "admin"), false);
  assert.equal(isPublicRoute("/shop/warenkorb?tenantId=other"), true);
  assert.equal(deniedRouteTarget(null), "/login");
  assert.equal(deniedRouteTarget("sales"), "/");
});
