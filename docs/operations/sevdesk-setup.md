# sevdesk – sichere spätere Einrichtung

Die Integration ist vorbereitet, aber nicht aktiviert. Ohne explizite
Konfiguration erzeugt ORDO nur einen `not_configured`-Status und sendet keine
Daten an sevdesk.

## Manuelle Schritte für Sergio

1. Ein getrenntes sevdesk-Testkonto bzw. eine ausdrücklich für Tests geeignete
   Organisation bereitstellen.
2. Einen API-Token mit den minimal notwendigen Rechten für Kontakte und
   Rechnungsentwürfe erzeugen. Den Token ausschließlich als Backend-Secret
   `SEVDESK_API_TOKEN` hinterlegen.
3. `ACCOUNTING_PROVIDER=sevdesk` setzen und den separaten Worker aktivieren:
   `BACKGROUND_JOBS_ENABLED=true`, `WORKER_SERVICE_ENABLED=true`.
4. Für Staging erst nach freigegebenem Testplan
   `SEVDESK_STAGING_WRITES_ENABLED=true` setzen. Ohne diesen Schalter verweigert
   der Adapter Schreibzugriffe.
5. Mapping für Adressen, Umsatzsteuer, Steuersätze, Erlöskonten,
   Rechnungsnummern, Zahlungsarten und rechtliche Pflichtangaben mit
   Steuerberatung/Buchhaltung bestätigen. ORDO erfindet diese Regeln nicht.
6. Mit fiktiven Daten je einen Kontakt und Rechnungsentwurf erzeugen, den
   Retry-Fall prüfen und Provider-Referenzen in ORDO kontrollieren.
7. Production erst nach erfolgreichem Abnahmetest separat konfigurieren und
   zusätzlich `SEVDESK_PRODUCTION_ENABLED=true` setzen. Staging-Token und
   Production-Token müssen getrennt bleiben.

## Idempotenz und Fehlerverhalten

- Kontakt: Suche über die unveränderliche ORDO-Kunden-ID als
  `customerNumber`, danach höchstens eine Erstellung.
- Rechnung: Suche über die ORDO-Dokument-ID als externe Notiz, danach höchstens
  ein Entwurf.
- Mehrere Provider-Treffer werden nicht automatisch zusammengeführt.
- Ein Provider-Ausfall lässt die ORDO-Rechnung und einen bereits bestätigten
  Zahlungsstatus unverändert. Der bestehende Job wird wiederholt.
- Der Operations-Bereich zeigt `pending`, `failed`, `completed` und die
  gespeicherten Provider-Referenzen. Ein manueller Retry wird nur bei einem
  echten Fehlschlag angeboten.

## Vor Aktivierung fachlich zu entscheiden

- Welche Entität ist Rechnungsaussteller für DE und CH?
- Wer vergibt die rechtlich maßgebliche Rechnungsnummer: ORDO oder sevdesk?
- Welche Steuer- und Erlöskonten gelten je Land, Produkt und Steuersatz?
- Soll ein Entwurf automatisch finalisiert oder immer manuell geprüft werden?
- Wie werden Gutschrift, Storno, Refund und Zahlungseingang synchronisiert?

Bis diese Entscheidungen getroffen und getestet sind, bleibt der Adapter
deaktiviert. Es dürfen keine echten Rechnungen erzeugt werden.
