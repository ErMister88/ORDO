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

## Next Tasks
- Add offer/invoice PDF + share.
- Product & price management screens for admin.
