# Financial Operations

## Verbindliche Systemgrenzen

ORDO bleibt die fachliche Quelle für Preise, Bestellungen, Rechnungen,
Zahlungsstände und Provisionen. Beträge werden als Integer in der kleinsten
Währungseinheit gespeichert. EUR und CHF werden getrennt geführt; ORDO nimmt
keine Wechselkursumrechnung vor.

- B2C verwendet ausschließlich den bestehenden Stripe-Checkout und signierte
  Webhooks.
- B2B akzeptiert ausschließlich Rechnung (`bank_transfer`) oder Barzahlung
  (`cash`). Ein B2B-Auftrag kann serverseitig keinen Stripe-Checkout erzeugen.
- Kunden können weder Zahlungsbedingungen noch Kredit- oder Palettenlimits
  verändern und keine Rechnung selbst als bezahlt markieren.
- Admins verwalten Finanzbedingungen und bestätigen manuelle Zahlungen.
- Der Vertrieb sieht nur zugewiesene Kunden und eigene Provisionen. Interne
  Kosten, Marge und Preisuntergrenzen bleiben verborgen.

## Zahlungsbedingungen und Forderungen

Die Finanzbedingungen liegen tenantbezogen am Kunden:

- `paymentTermsDays`: Zahlungsziel in Tagen
- `creditLimitMinor`: Kreditlimit in Minor Units, Standard 1.000.000
  (10.000,00 der konfigurierten Währung)
- `creditCurrency`: `EUR` oder `CHF`
- `palletApprovalLimit`: Palettenäquivalent, Standard 1

Eine Rechnung übernimmt das Zahlungsziel als unveränderlichen Snapshot und
berechnet daraus das Fälligkeitsdatum. Der Forderungsstatus wird aus
Rechnungsbetrag, bestätigten Zahlungen und Fälligkeitsdatum ermittelt:
`OPEN`, `DUE`, `OVERDUE`, `PARTIALLY_PAID`, `PAID` oder `CANCELLED`.
Die Aging-Sicht gruppiert offene Beträge in noch nicht fällig, 1–7, 8–30,
31–60 und mehr als 60 Tage überfällig.

Verfügbarer Kredit ist:

`Kreditlimit − offene Forderungen − noch nicht fakturierte Aufträge`

Die Prüfung verwendet nur dieselbe Währung. Eine abweichende oder ungültige
Konfiguration führt sicher in die Freigabe, niemals zu einer stillen
Umrechnung.

## Palettenprüfung und Freigaben

Produkte können `kgPerPallet`, `casesPerPallet`, `kgPerCase` und
`unitsPerCase` speichern. Das Backend berechnet gemischte Aufträge je Position
in ein Palettenäquivalent um. Fehlt für eine Position eine belastbare
Konfiguration, wird der Auftrag zur Prüfung markiert.

Eine Finanzfreigabe wird unter anderem erforderlich bei:

- gesperrtem Kunden,
- fehlender oder ungültiger Finanzkonfiguration,
- überschrittenem verfügbaren Kredit,
- überfälligen Forderungen,
- fehlender Palettenkonfiguration,
- überschrittenem Kunden-Palettenlimit.

Die Gründe enthalten keine Margen-, Kosten- oder Price-Floor-Daten. Nur ein
Admin kann freigeben oder ablehnen. Bei einem Auftrag mit gewünschter Rechnung
wird die Rechnung erst nach der Freigabe idempotent erzeugt.

## Accounting Provider

`app.accounting` definiert eine kleine providerneutrale Schnittstelle für
Kontakt- und Rechnungsabgleich. Der vorhandene sevdesk-Adapter ist standardmäßig
deaktiviert. ORDO persistiert zuerst den fachlichen Vorgang, danach einen
tenantbezogenen Sync-Auftrag. Provider-Ausfälle verändern weder Zahlung noch
Rechnung; der bestehende Worker wiederholt sicher und das Operations Center
zeigt Fehler und Provider-Referenzen.

Der Idempotenzschlüssel basiert auf Ressourcentyp und ORDO-ID. Externe Schlüssel
enthalten immer die unveränderliche Tenant-ID. Der Adapter sucht vor dem
Erzeugen nach diesem ORDO-Schlüssel. Mehrdeutige Treffer führen
in eine manuelle Prüfung. Ein Wiederanlauf repariert auch den engen Absturzfall,
in dem der Provider-Erfolg bereits gespeichert wurde, die Referenz am
ORDO-Dokument jedoch noch fehlt.

## Provisionsledger

Provisionsvereinbarungen sind tenantbezogen und unterstützen derzeit nur
`PER_KG`. Es gibt keine fest eingebaute Rate. Wenn mehrere Vereinbarungen auf
dieselbe Position passen, stoppt die Bestätigung fail-closed, weil keine
fachliche Prioritätsregel definiert ist.

- Bei qualifizierter Auftragsbestätigung entsteht ein `PENDING`-Eintrag mit
  Vereinbarungs-, Vertriebs-, Kunden-, Produkt-, Mengen- und
  Währungssnapshot.
- Erst eine bestätigte Zahlung erzeugt `EARNED`-Einträge.
- Teilzahlungen verwenden disjunkte Zahlungsintervalle. Parallele Verarbeitung
  kann dadurch nicht mehr als die vorgemerkte Provision verdienen.
- Gutschrift, Refund, Storno und Korrektur erzeugen append-only
  `ADJUSTED`-/`REVERSED`-Einträge; historische Beträge werden nicht
  überschrieben.
- Ein Settlement besitzt einen expliziten UTC-Zeitraum, enthaltene Ledger-IDs,
  Währung und Betrag. Es wird nach Erstellung gesperrt.
- Nur ein Admin kann ein Settlement mit einer Referenz als `PAID_OUT`
  markieren; Wiederholungen sind idempotent.

Die automatische fachliche Zuordnung von Refunds, Credits oder Stornos zu
Provisionskorrekturen ist noch nicht festgelegt. Bis dahin erfolgen solche
Korrekturen über den ausdrücklich autorisierten Adjustment-Weg.

## Integrität und Wiederherstellung

- Alle neuen Collections laufen über `TenantBusinessAccess`.
- Clientseitige Werte besitzen keine Autorität über Tenant, Finanzbedingungen,
  Kreditlimit, Palettenlimit, Provisionsrate oder Zahlungsstatus.
- Accounting-, Ledger-, Settlement- und Payout-Vorgänge besitzen eindeutige
  tenantbezogene Idempotenzschlüssel bzw. atomare Statuswechsel.
- Reconciliation prüft Order–Invoice, Invoice–Payment,
  Payment–Commission, Commission–Settlement und ORDO–Accounting-Referenzen
  read-only. Es korrigiert keine Geschäftsdaten automatisch.

## Konfiguration

Die Accounting-Variablen sind Backend-Secrets bzw. serverseitige Schalter:

- `ACCOUNTING_PROVIDER` (leer oder `sevdesk`)
- `SEVDESK_BASE_URL`
- `SEVDESK_API_TOKEN`
- `SEVDESK_STAGING_WRITES_ENABLED`
- `SEVDESK_PRODUCTION_ENABLED`
- `BACKGROUND_JOBS_ENABLED`
- `WORKER_SERVICE_ENABLED`

Stripe verwendet weiterhin `PAYMENTS_ENABLED`, `STRIPE_API_KEY`,
`STRIPE_WEBHOOK_SECRET` und `APP_URL`. Kein Secret gehört ins Frontend oder in
Git.

## Release-Voraussetzungen

1. Migration 16 zuerst als Dry Run und danach nur gegen die freigegebene
   Zielumgebung ausführen.
2. Schema-Version, Readiness und tenantbezogene Indizes prüfen.
3. Worker nur gemeinsam mit aktivierten Background Jobs betreiben.
4. B2B-Rechnung/Bar, Freigabe, Forderung, Teilzahlung und Provision mit
   fiktiven Staging-Daten prüfen.
5. Stripe nur im Testmodus verwenden.
6. sevdesk deaktiviert lassen, bis ein getrenntes Testkonto, Datenmapping und
   eine ausdrückliche Schreibfreigabe geprüft sind.
7. Vor Production Backup und Restore-Test, Alerting und Reconciliation-Lauf
   nachweisen.
