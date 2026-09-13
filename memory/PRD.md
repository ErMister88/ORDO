# S&S Großhandel — B2B Mobile App (PRD)

## Original Problem Statement
"Erstellen Sie eine mobile App" — rebuild of an uploaded B2B wholesale app (ss-b2b-mobile.zip) for S&S, a coffee & food-service wholesaler in Germany. Original was plain React Native + Supabase with local demo data. Rebuilt on Expo + FastAPI + MongoDB.

## User Choices
- **Auth**: JWT email/password login with seeded admin/sales/customer accounts.
- **Data**: Backend seeded with demo data (Ristorante Roma, Bar Milano, Eis Venezia, Caffè Torino + coffee products).
- **Design**: Modern fintech/SaaS — deep navy/blue accent (#0B1B3D / #1D3B8E), crisp white cards, data-forward.
- **Extras**: Customer follow-up reminders + analytics charts.

## Architecture
- **Backend**: FastAPI + Motor (MongoDB). JWT (PyJWT) + bcrypt hashing. Role-based visibility (admin=all, sales=assigned companies, customer=own company). Idempotent startup seeding with atomic sequence counters + unique indexes on offers/orders.
- **Frontend**: Expo Router. `@tanstack/react-query` for data. `phosphor-react-native` icons, `react-native-gifted-charts` charts, `expo-image`/`expo-linear-gradient`. Theme tokens in `src/theme.ts`. Secure token storage via `@/src/utils/storage`.
- **Language**: German (de-DE) throughout, EUR formatting.

## User Personas
- **Admin (Sergio)**: full oversight, approves/rejects price offers, sees all customers & analytics.
- **Sales (Marco)**: manages assigned customers (c1/c3/c4), creates offers, analytics.
- **Customer (Ristorante Roma)**: reorders, views contracts & invoices.

## Seeded Accounts (see /app/memory/test_credentials.md)
- admin@ss-coffee.de / Admin#2026
- vertrieb@ss-coffee.de / Sales#2026
- kunde@ss-coffee.de / Kunde#2026

## Implemented (2026-06-13)
- JWT login with navy hero + demo-account chips
- Role-aware bottom tabs (max 4 per role)
- Dashboard: hero metric, KPI tiles, price-approval alert, follow-up reminders (overdue customers)
- Kunden list: search + filter chips (Alle/Überfällig/Aktuell); customer detail with Konditionen/Historie segmented control + "Neues Angebot" CTA
- Angebote: create with sales-floor / absolute-floor validation; admin approve/reject
- Bestellungen: customer reorder stepper + order history
- Auswertungen: line + bar charts, timeframe (3M/6M/12M) & metric (Umsatz/Marge/Menge) toggles, summary stat
- Mehr: contracts + invoices + logout (customer)
- Backend: 26/26 pytest cases pass; all role flows verified

## Backlog
- **P1**: Offer PDF export / share; invoice document (signed URL) viewing.
- **P1**: Push customer follow-up nudges (deploy + build required).
- **P2**: Admin CRUD for products & customer prices in-app.
- **P2**: Order status progression (Bestätigt → Kommissioniert → Versendet).
- **P2**: Multi-item offers/orders (currently single line item in UI).

## Design
- Dark premium theme (near-black canvas #0B0C11, elevated cards, gold accent #E7B24C), rounder cards/buttons, more spacing, bold KPI hero. All tokens in src/theme.ts.
- Branding: in-app "ORDO Connect by S&S" (login), app store / build name "Ordo Connect" (app.json name).

## Implemented (2026-06-13, Iteration 6)
- **Angebot → Bestellung 1-Tap**: `POST /api/offers/{id}/accept` (customer) wandelt ein freigegebenes Angebot in eine Bestellung um (offer→Angenommen + orderId, order.fromOffer). UI-Button in Angebote-Tab + Sprung zur Bestellung.
- **Mengenrabatt-Staffeln**: Produkt-Feld `discountTiers:[{minQty,price}]`; Admin-Editor in /produkte; automatische Staffelpreise in Angebotserstellung (apply-tier) und Kunden-Nachbestellung (tier-active). Seed p1/p2 mit Staffeln.
- **E-Mail-Benachrichtigungen (Resend, Emergent-managed)**: Mail an Firmen-E-Mail bei Angebot-Freigabe (`/approve`) und Bestell-Versand (`status=Versendet`). Nicht-blockierend (try/except), Absender `EMAIL_FROM_NAME` = "ORDO Connect by S&S". Guardrail-Gate `_assert_safe_email` auf jedem Send.

- **B2C MwSt & Bestätigung**: MwSt-Aufschlüsselung (7% Kaffee / 19% Maschine) in Katalog/Warenkorb/Bestellung; Bestellbestätigung per E-Mail (Resend) mit MwSt-Ausweis bei Shop-Bestellung. Demo-Maschine `m1` (19%) angelegt.
- **Noch offen**: B2C-Konten (Registrierung/Login mit Bestellverlauf) – aktuell nur Gast-Checkout.

## Implemented (2026-06-13, Iteration 11) — B2C-Konten & Shop-Mails
- **B2C-Konten**: `POST /api/shop/register`, `/shop/login`, `GET /shop/me`, `/shop/my-orders` (Rolle `shopuser`, eigenes Token via `src/shop/auth.ts`, Screen `/shop/konto`, „Konto"-Button im Shop-Header). Bestellungen eingeloggter Nutzer werden per `userId` verknüpft; Gast-Checkout bleibt möglich.
- **Shop-E-Mails**: Bestellbestätigung bei Bestellung + „Zahlung erhalten" bei bezahlter Zahlung (Resend, mit MwSt-Ausweis).

## Implemented (2026-06-13, Iteration 10) — B2C-Shop
- **Öffentlicher Shop** (ohne Login, erreichbar über Login → „Zum Kaffee-Shop"): `GET /api/shop/products` (public, kein cost-Leak), Katalog `/shop`, Warenkorb `/shop/warenkorb` (Cart-Context `src/shop/cart.tsx`).
- **Gast-Checkout**: `POST /api/shop/orders` (serverseitige Preisberechnung aus `b2cPrice`), Versand gratis ab Schwelle, sonst Pauschale; Bestellnr. `S-YYYY-#####`.
- **Zahlung**: Stripe Checkout mit dynamischen Zahlarten (KEINE feste `payment_method_types` → Karte + PayPal + Klarna/Sofortüberweisung, sofern im Stripe-Konto aktiviert), `currency=eur`, `locale=de`, `customer_email`. Auch Rechnungs-Checkout (B2B) nutzt dynamische Methoden. SOFORT-Standalone von Stripe abgekündigt → Sofortüberweisung via Klarna „Pay now". Erst nach Deploy live.
- **Admin**: B2C-Preis je Produkt (`b2cPrice` im Produktformular), Shop-Verwaltung `/shop-admin` (Versand-Schwelle/-kosten + B2C-Bestellliste). `GET/PUT /api/shop/settings`, `GET /api/shop/orders`.
- **Noch offen**: B2C-Konten (Registrierung/Login mit Bestellverlauf) – aktuell nur Gast-Checkout.

## Implemented (2026-06-13, Iteration 9)
- **Schnell-Lagerbestand**: `PUT /api/products/{id}/stock` (admin) + Inline-Control (−/∞/+ /Speichern) auf jeder Produktkarte; Audit `product.stock`.
- **Mehrere Positionen beim Nachbestellen**: Warenkorb in Bestellungen (Produktauswahl, Menge, hinzufügen/entfernen, Staffelpreis + Lager-Warnung je Position).
- **Suche/Filter in Bestellungen**: Text (Bestellnr.) + Status-Chips.
- **Angebots-Notiz**: optionale Notiz bei Angebots-Annahme → `order.customerNote`, Anzeige in Bestelldetails.
- **Audit-Log**: `db.audit_log` + `GET /api/audit` (nur Admin); protokolliert login, user.create/reset, company.update, price.set, order.status, invoice.paid, offer.approve/accept. Screen `/audit` (Mehr → Verwaltung).

## Architecture (Iteration 8 — modularized)
Backend split into a package: `backend/app/` with `core.py` (config/db/security), `emailer.py`, `storage.py`, `deps.py`, `models.py`, `seed.py`, `main.py`, and `routers/*` (auth, users, products, pricing, companies, offers, orders, invoices, dashboard, analytics, subscriptions, billing, payments). Entry stays `server:app` → `app.main:app`.

## Implemented (2026-06-13, Iteration 8)
- **Firmenverwaltung**: `PUT /api/companies/{id}` (admin) + Editor in kunde/[id].tsx.
- **Erst-Login Passwortzwang**: `must_change_password` in PublicUser; Login/Index leiten zu `/passwort-aendern?forced=1`. **Login-Ratenbegrenzung**: 429 nach 5 Fehlversuchen.
- **Rechnungen mit MwSt**: `POST /api/orders/{id}/invoice` erzeugt fortlaufende RE-Nummer + MwSt-Aufschlüsselung (Produkt-`taxRate` 7/19), Netto/MwSt/Brutto; PDF mit MwSt.
- **Sammelrechnung**: `GET /api/companies/{id}/collective-invoice?year&month` → PDF. **Lieferschein-PDF** je versendeter Bestellung.
- **Abo-Bestellungen**: `/api/subscriptions` CRUD + `/run`; Screen `/abos`.
- **Produkt**: `taxRate` (7/19) + `stock` (Warnhinweis) im Formular & Kartenanzeige.
- **Suche/Filter** in Angebote (Text + Status-Chips).
- **Online-Zahlung (Stripe Testmodus)**: `/api/invoices/{id}/checkout` + `/payment-status`; nur nach Deploy testbar (Key ist Platzhalter im Preview).

## Noch offen (Backlog aus diesem Auftrag)
- Mehrere Positionen in der Kunden-Nachbestellung (aktuell 1 Produkt)
- Suche/Filter auch in Bestellungen
- Angebots-Annahme mit Notiz
- Audit-Log

## Design (theme)
- Dark premium theme tokens in src/theme.ts.

## Implemented (2026-06-13, Iteration 7)
- **Produktbilder** in Angebots-/Bestellpositionen & Kunden-Nachbestellung (Platzhalter-Icon wenn kein Bild).
- **Produkt aktiv/inaktiv**: `PUT /api/products/{id}/active` (admin); GET /products liefert alle inkl. `active` (cost weiter rollenabhängig versteckt); inaktive Produkte erscheinen nicht in Angebots-/Bestell-Auswahl, Historie bleibt lesbar; Toggle in /produkte.
- **Produktsuche** in /produkte (Marke/Name/Beschreibung, client-seitig).
- **Benutzerverwaltung** (`/benutzer`, admin): `GET/POST /api/users`, `POST /api/users/{id}/reset`; Vertrieb+Kunden anlegen, Firma bestehend wählen oder neu erstellen, Zufalls-Passwort einmalig angezeigt.
- **Passwort-Reset**: self-service `/auth/password/forgot` + `/auth/password/reset` (6-stelliger Code per E-Mail, sha256-gehasht, single-use, 30 Min TTL) via Login „Passwort vergessen?"; `/auth/password/change` (angemeldet) via Mehr → „Passwort ändern".

## Next Tasks
- Optional light-mode toggle if ever requested.
- Show product images across offer/order line items.
