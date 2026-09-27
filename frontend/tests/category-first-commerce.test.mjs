import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("shop starts with categories and keeps curated discovery", () => {
  const home = read("app/shop/index.tsx");
  assert.match(home, /shop\/discovery/);
  assert.match(home, /Kategorien/);
  assert.match(home, /HomepageBlock/);
  assert.match(home, /Neu im Shop/);
});

test("catalog uses server-side search filters pagination and sorting", () => {
  const screen = read("src/shop/catalog-screen.tsx");
  assert.match(screen, /shop\/catalog\?/);
  assert.match(screen, /attribute/);
  assert.match(screen, /pageSize/);
  assert.match(screen, /catalog\.data\.pages/);
  assert.match(screen, /setPage/);
  assert.match(screen, /minPrice/);
  assert.match(screen, /brandFilter/);
  assert.match(screen, /price_asc/);
  assert.match(screen, /width >= 1024/);
  assert.match(screen, /Modal/);
});

test("product detail requires variant selection and uses cart variant snapshot", () => {
  const detail = read("app/produkte/[slug].tsx");
  assert.match(detail, /!variants\.length \|\| Boolean\(variantId\)/);
  assert.match(detail, /variantId: variantId/);
  assert.match(detail, /basePrice/);
  assert.doesNotMatch(detail, /costMinor|salesFloor|absoluteFloor|priceFloor/);
});

test("commerce admin separates catalog concepts", () => {
  const admin = read("app/commerce-admin.tsx");
  for (const term of ["Kategorien", "Marken", "Collections", "Attribute", "Regionen", "Versandklassen", "Bundles", "Startseite"]) {
    assert.match(admin, new RegExp(term));
  }
  assert.match(admin, /Standardsortierung/);
  assert.match(admin, /Gültig für Kategorien/);
  assert.match(admin, /Ausgewählter Inhalt/);
});

test("cart forwards variants and visualises configured free shipping threshold", () => {
  const cart = read("app/shop/warenkorb.tsx");
  const cartState = read("src/shop/cart.tsx");
  assert.match(cart, /variantId: i\.variantId/);
  assert.match(cartState, /replacingVariant \? qty : i\.qty \+ qty/);
  assert.match(cart, /threshold > 0/);
  assert.match(cart, /shippingProgress/);
  assert.doesNotMatch(cart, /freeShippingThreshold\s*=\s*\d/);
});

test("product administration supports a managed multi-image gallery", () => {
  const products = read("app/produkte.tsx");
  assert.match(products, /allowsMultipleSelection: true/);
  assert.match(products, /Als Hauptbild/);
  assert.match(products, /moveGalleryImage/);
});

test("public commerce pages provide metadata and region discovery", () => {
  const seo = read("src/shop/seo.ts");
  const home = read("app/shop/index.tsx");
  assert.match(seo, /og:title/);
  assert.match(seo, /canonical/);
  assert.match(home, /regionen\//);
});

test("new commerce copy has DE IT EN catalogue coverage", () => {
  const index = read("src/i18n/index.tsx");
  const translations = read("src/i18n/translations-commerce.ts");
  assert.match(index, /commerceTranslations/);
  for (const key of ["Gute Produkte. Klar sortiert.", "In den Warenkorb", "Kostenloser Versand erreicht.", "Commerce-Verwaltung", "Variante wählen"]) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(translations, new RegExp(escaped));
  }
});
