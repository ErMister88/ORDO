import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

const orders = read("../app/(tabs)/bestellungen.tsx");
const customer = read("../app/kunde/[id].tsx");
const products = read("../app/produkte.tsx");
const operations = read("../app/systembetrieb.tsx");
const sales = read("../app/vertrieb/[id].tsx");
const reports = read("../app/(tabs)/auswertungen.tsx");

test("B2B checkout exposes invoice and cash only and uses server approval state", () => {
  assert.match(orders, /\[\["bank_transfer", "Rechnung"\], \["cash", "Barzahlung"\]\]/);
  assert.doesNotMatch(orders, /\["card",/);
  assert.match(orders, /result\?\.financialApproval\?\.required/);
  assert.match(orders, /\/financial\/approvals\/\$\{orderId\}\/\$\{decision\}/);
});

test("customer finance UI keeps credit controls admin-only", () => {
  assert.match(customer, /isAdmin \? <InfoRow label="Kreditlimit"/);
  assert.match(customer, /isAdmin && !editing \? <Button title="Finanzkonditionen bearbeiten"/);
  assert.match(customer, /\/companies\/\$\{companyId\}\/financial-terms/);
});

test("product administration persists pallet calculation inputs", () => {
  for (const field of ["unitsPerCase", "kgPerCase", "casesPerPallet", "kgPerPallet"]) {
    assert.match(products, new RegExp(`${field}:`));
  }
});

test("operations retries only failed accounting jobs", () => {
  assert.match(operations, /sync\.status === "failed"/);
  assert.doesNotMatch(operations, /\["failed", "not_configured"\]\.includes/);
});

test("commission screens separate own reporting from admin settlement actions", () => {
  assert.match(reports, /user\?\.role === "admin" \|\| user\?\.role === "sales"/);
  assert.match(reports, /values\.pendingQty/);
  assert.match(reports, /values\.earnedQty/);
  assert.match(reports, /row\.agreementSnapshot\?\.rateMinor/);
  assert.match(sales, /isAdmin && row\.status === "LOCKED"/);
  assert.match(sales, /apiPostIdempotent\("\/commission\/settlements"/);
  assert.match(sales, /\/commission\/settlements\/\$\{settlementId\}\/payout/);
  assert.match(sales, /\/commission\/agreements\/\$\{agreement\.id\}/);
});
