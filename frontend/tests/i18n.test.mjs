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
    "Einlösen",
    "AGB",
    "Jetzt bezahlen",
    "Zahlung wird bestätigt",
    "Zahlung bestätigt",
    "Zahlung nicht abgeschlossen",
    "Bestehender Kunde",
    "Interessent",
    "Angebotsempfänger",
    "Mögliche Dublette",
    "Stammdaten vervollständigen",
    "Neuer Vertriebler",
    "Als Kunde anlegen",
  ]) {
    assert.ok(catalog[source], `missing critical translation: ${source}`);
  }
});

test("final review terminology is professionally localized without raw German", () => {
  const keys = [
    "Bestehender Kunde",
    "Angebotsempfänger",
    "Mögliche Dublette",
    "Stammdaten vervollständigen",
    "Trotzdem als Kunde anlegen",
    "Neuer Vertriebler",
  ];
  for (const source of keys) {
    assert.ok(catalog[source], `missing final-review translation: ${source}`);
    assert.notEqual(catalog[source].it, source);
    assert.notEqual(catalog[source].en, source);
  }
  assert.equal(catalog["Interessent"].en, "Prospect");
  assert.equal(catalog["Angebotsempfänger"].it, "Destinatario dell'offerta");
});

test("language switching is persistent and outside session/cart providers", async () => {
  const i18n = await readFile(new URL("../src/i18n/index.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/_layout.tsx", import.meta.url), "utf8");
  assert.match(i18n, /ordo_ui_language/);
  assert.match(i18n, /storage\.getItem/);
  assert.match(i18n, /storage\.setItem/);
  assert.match(i18n, /languageSelected\.current/);
  assert.ok(layout.indexOf("<I18nProvider>") < layout.indexOf("<AuthProvider>"));
  assert.ok(layout.indexOf("<I18nProvider>") < layout.indexOf("<CartProvider>"));
});

test("translation catalog contains UI copy only and no tenant payload", () => {
  const serialized = JSON.stringify(catalog);
  assert.doesNotMatch(serialized, /tnt_ss_0001|companyId|customerId|passwordHash|authVersion/);
});

test("dynamic shop translations preserve every interpolation placeholder", () => {
  const dynamicKeys = [
    "Gratis-Versand ab {threshold}",
    "Newsletter & {percent}% Rabatt",
    "inkl. {rate}% MwSt",
    "ab {quantity} · {price}/{unit}",
    "Bestellnummer {id} · Summe {total} · {status}",
    "Kostenpflichtig bestellen · {total}",
    "{orders} Bestellungen im sichtbaren Kundenbestand",
    "{count} Angebot(e) warten auf Ihre Freigabe",
    "{days} Tage seit letzter Bestellung",
    "{orders} Bestellungen · {activeCustomers} aktive Kunden",
    "{orders} Bestellungen · Menge {quantity}",
    "Rechnung {id} ist überfällig",
    "DB {amount}",
    "{quantity} kg/Monat",
    "{months} Monate",
    "{amount}/Monat",
  ];
  const placeholders = (value) => [...value.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map((match) => match[1]).sort();

  for (const source of dynamicKeys) {
    assert.ok(catalog[source], `missing dynamic translation: ${source}`);
    assert.deepEqual(placeholders(catalog[source].it), placeholders(source), `Italian placeholders differ for ${source}`);
    assert.deepEqual(placeholders(catalog[source].en), placeholders(source), `English placeholders differ for ${source}`);
  }
});

test("dashboard metrics and shared field accessibility do not leak German in IT/EN", async () => {
  const dashboard = await readFile(new URL("../app/(tabs)/index.tsx", import.meta.url), "utf8");
  const ui = await readFile(new URL("../src/components/ui.tsx", import.meta.url), "utf8");
  for (const source of [
    "{orders} Bestellungen im sichtbaren Kundenbestand",
    "{days} Tage seit letzter Bestellung",
    "{orders} Bestellungen · {activeCustomers} aktive Kunden",
    "{orders} Bestellungen · Menge {quantity}",
    "Rechnung {id} ist überfällig",
  ]) {
    assert.match(dashboard, new RegExp(`tf\\(${JSON.stringify(source).replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}`));
  }
  assert.match(ui, /accessibilityLabel=\{t\(title\)\}/);
  assert.match(ui, /props\.accessibilityLabel \? t\(props\.accessibilityLabel\)/);
  assert.equal(catalog["DB nicht vollständig"].en, "Contribution margin incomplete");
  assert.equal(catalog["DB nicht vollständig"].it, "Margine di contribuzione incompleto");
});
