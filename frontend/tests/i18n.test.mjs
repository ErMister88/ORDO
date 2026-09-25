import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { extraTranslations } from "../src/i18n/translations-extra.ts";
import { coreTranslations } from "../src/i18n/translations.ts";

const catalog = { ...coreTranslations, ...extraTranslations };

test("catalog provides complete Italian and English translations", () => {
  assert.ok(Object.keys(catalog).length >= 650);
  assert.equal(
    Object.keys(catalog).length,
    Object.keys(coreTranslations).length + Object.keys(extraTranslations).length,
    "translation keys must be unique across catalog modules",
  );
  for (const [source, value] of Object.entries(catalog)) {
    assert.equal(typeof value.it, "string", `missing Italian translation for ${source}`);
    assert.equal(typeof value.en, "string", `missing English translation for ${source}`);
    assert.ok(value.it.trim(), `empty Italian translation for ${source}`);
    assert.ok(value.en.trim(), `empty English translation for ${source}`);
  }
});

test("critical B2C, B2B, admin and payment UI has translations", () => {
  for (const source of [
    "Anmelden",
    "Dashboard",
    "Kunden",
    "Produktkatalog",
    "Produkte",
    "Angebote",
    "Bestellungen",
    "Rechnungen",
    "Einstellungen",
    "Kundenportal",
    "B2C-Shop",
    "Warenkorb",
    "Jetzt bezahlen",
    "Zahlung wird bestätigt",
    "Zahlung bestätigt",
    "Zahlung nicht abgeschlossen",
  ]) {
    assert.ok(catalog[source], `missing critical translation: ${source}`);
  }
});

test("language switching is persistent and outside session/cart providers", async () => {
  const i18n = await readFile(new URL("../src/i18n/index.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/_layout.tsx", import.meta.url), "utf8");
  assert.match(i18n, /ordo_ui_language/);
  assert.match(i18n, /storage\.getItem/);
  assert.match(i18n, /storage\.setItem/);
  assert.ok(layout.indexOf("<I18nProvider>") < layout.indexOf("<AuthProvider>"));
  assert.ok(layout.indexOf("<I18nProvider>") < layout.indexOf("<CartProvider>"));
});

test("translation catalog contains UI copy only and no tenant payload", () => {
  const serialized = JSON.stringify(catalog);
  assert.doesNotMatch(serialized, /tnt_ss_0001|companyId|customerId|passwordHash|authVersion/);
});
