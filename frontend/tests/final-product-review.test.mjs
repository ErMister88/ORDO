import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("quick create is role-aware and available on compact layouts without header collision", async () => {
  const shell = await read("../src/components/app-shell.tsx");
  assert.match(shell, /MobileQuickCreate/);
  assert.match(shell, /global-new-mobile/);
  assert.match(shell, /bottom: 76/);
  assert.match(shell, /MobileUtilityBar/);
  assert.match(shell, /mobile-utility-bar/);
  assert.match(shell, /sidebarFooter/);
  assert.doesNotMatch(shell, /languageSwitcherFloating/);
  assert.match(shell, /publicLanguageBar/);
  assert.match(shell, /publicRoute \? <View style=\{styles\.publicLanguageBar\}>/);
  assert.match(shell, /role === "admin"/);
  assert.match(shell, /role === "admin" \|\| role === "sales"/);
  assert.match(shell, /actions\.length/);
  for (const label of ["Neuer Kunde", "Neues Angebot", "Neue Bestellung", "Neue Rechnung", "Neuer Vertriebler"]) {
    assert.match(shell, new RegExp(label));
  }
});

test("offer UI supports prospect snapshots and server pricing without a fake company", async () => {
  const offers = await read("../app/(tabs)/angebote.tsx");
  assert.match(offers, /offer-target-prospect/);
  assert.match(offers, /prospectRecipient/);
  assert.match(offers, /pricing\/b2b\/prospect-quote/);
  assert.match(offers, /offer-to-customer/);
  assert.match(offers, /existingCompanyId/);
  assert.match(offers, /Mit \{name\} verknüpfen/);
  assert.match(offers, /prospect-details-toggle/);
  assert.match(offers, /Weitere Daten hinzufügen/);
  assert.match(offers, /disabled=\{items\.length === 0 \|\| \(targetMode === "customer" \? !companyId : !recipient\.name\.trim\(\)\)\}/);
  assert.match(offers, /offer-customer-form-/);
  assert.match(offers, /prospectCustomerForm/);
  assert.match(offers, /historischer Snapshot unverändert/);
  assert.doesNotMatch(offers, /fakeCompany|temporaryCompany/i);
});

test("quick customer creation requires only a name and warns before duplicates", async () => {
  const customers = await read("../app/(tabs)/kunden.tsx");
  const customerDetail = await read("../app/kunde/[id].tsx");
  assert.match(customers, /companies\/duplicate-check/);
  assert.match(customers, /confirmPotentialDuplicate/);
  assert.match(customers, /disabled=\{!form\.name\.trim\(\)\}/);
  assert.match(customerDetail, /master-data-incomplete/);
});

test("user administration never renders or consumes generated passwords", async () => {
  const users = await read("../app/benutzer.tsx");
  assert.doesNotMatch(users, /initialPassword|cred-password|einmalig sichtbar/);
  assert.match(users, /invitationQueued/);
  assert.match(users, /assignedCustomerCount/);
  assert.match(users, /\["admin", "sales", "customer"\]/);
  assert.match(users, /apiPut\(`\/users\/\$\{id\}\/status`/);
  assert.match(users, /u\.id !== user\?\.id/);
});
