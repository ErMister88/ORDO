import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("admin and sales share one server-backed offer action workflow", async () => {
  const offers = await read("../app/(tabs)/angebote.tsx");
  assert.match(offers, /isStaff && o\.status === "Freigegeben"/);
  assert.match(offers, /offer-pdf-view-/);
  assert.match(offers, /offer-pdf-download-/);
  assert.match(offers, /offer-secure-link-/);
  assert.match(offers, /offer-email-/);
  assert.match(offers, /\/offers\/\$\{offer\.id\}\/send/);
  assert.match(offers, /saveRecipientEmail/);
  assert.match(offers, /E-Mail-Versand nicht konfiguriert/);
});

test("public recipient page needs no account and supports both decisions", async () => {
  const page = await read("../app/angebot/[token].tsx");
  const routes = await import("../src/auth/route-access.ts");
  assert.equal(routes.isPublicRoute("/angebot/random-token"), true);
  assert.match(page, /publicApiGet/);
  assert.match(page, /publicApiPost/);
  assert.match(page, /public-offer-accept/);
  assert.match(page, /public-offer-decline/);
  assert.match(page, /PDF anzeigen \/ herunterladen/);
  assert.doesNotMatch(page, /costMinor|priceFloor|salesAttribution|commission/i);
});

test("offer delivery copy is catalogued in Italian and English", async () => {
  const translations = await import("../src/i18n/translations-extra.ts");
  for (const key of [
    "PDF anzeigen", "PDF herunterladen", "Sicheren Link kopieren", "Per E-Mail senden",
    "Ihre Entscheidung", "Für die Antwort ist kein ORDO-Konto erforderlich.",
  ]) {
    assert.ok(translations.extraTranslations[key]?.it, `missing IT: ${key}`);
    assert.ok(translations.extraTranslations[key]?.en, `missing EN: ${key}`);
  }
});
