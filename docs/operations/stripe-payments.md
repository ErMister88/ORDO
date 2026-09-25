# Stripe-Zahlungsbetrieb

## Autorität und Ablauf

ORDO berechnet Betrag, Währung, Preis, Steuer und Rabatt serverseitig und
speichert diese Werte als fachlichen Snapshot. Stripe verarbeitet die Zahlung
und bestätigt ihren Providerstatus über einen signierten Webhook. Eine
Browser-Rückleitung oder eine Statusabfrage beim Browser markiert in ORDO keine
Zahlung als bezahlt.

Der Ablauf ist:

1. ORDO legt oder lädt einen idempotenten Checkout-Vorgang.
2. ORDO erstellt die Stripe Checkout Session mit serverseitigem Betrag und
   einer Stripe-Idempotency-Key.
3. ORDO speichert Session, erwarteten Betrag, Währung, Tenant und interne
   Ressource.
4. Stripe sendet ein signiertes Ereignis an das Backend.
5. ORDO prüft die Signatur am unveränderten Request-Body, beansprucht das
   Ereignis im Ledger und prüft Tenant, Ressource, Session, Operation, Betrag,
   Währung und Zahlungsstatus.
6. Erst danach wird die Rechnung, Shop-Bestellung oder Maschinenanfrage mit
   einem Payment-Snapshot atomar aktualisiert.

## Benötigte Konfiguration

Die Werte werden ausschließlich als Backend-Secrets konfiguriert:

- `STRIPE_API_KEY`: Staging/Test verwendet einen Test-Key, Production einen
  Live-Key.
- `STRIPE_WEBHOOK_SECRET`: Signing Secret des jeweiligen Webhook-Endpunkts.
- `APP_URL`: öffentliche Frontend-Adresse; in Staging und Production zwingend
  HTTPS.

Diese Werte dürfen weder im Frontend noch in Git, Logs oder API-Antworten
erscheinen. Staging und Production benötigen getrennte Stripe-Konfigurationen.
ORDO verweigert in Staging einen Live-Key und in Production einen Test-Key.

## Webhook

- URL: `https://api.<domain>/api/payments/stripe/webhook`
- Methode: `POST`
- Verifikation: offizieller Stripe-Signaturmechanismus mit dem unveränderten
  Request-Body und dem Header `Stripe-Signature`
- Antwort auf bereits verarbeitetes Ereignis: erfolgreicher Duplicate-Status,
  ohne zweiten wirtschaftlichen Effekt
- Temporärer interner Fehler: HTTP 503, damit Stripe erneut zustellt
- Terminaler Zuordnungsfehler: im Ledger sichtbar, ohne Zahlung zu verbuchen

Folgende Ereignisse müssen im Stripe Dashboard für den Endpoint aktiviert
werden:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`

Refund- und Dispute-Ereignisse werden derzeit nur als prüfpflichtig
klassifiziert. Eine automatische Rückerstattungs- oder Chargeback-Buchung ist
nicht implementiert und benötigt eine fachliche Entscheidung.

## Event-Ledger und Wiederholung

`payment_provider_events` enthält nur eine reduzierte, sichere
Ereigniszusammenfassung. Raw Payloads, Signaturen, Karteninformationen und
Secrets werden nicht gespeichert. Provider-Konto plus Event-ID ist global
eindeutig; jeder Datensatz trägt zusätzlich den serverseitig aufgelösten
Tenant.

Zustände:

- `processing`
- `processed`
- `failed_retryable`
- `failed_terminal`

Das Ledger wird bewusst nicht per TTL gelöscht, weil es für Abgleich,
Support, Audit und Duplicate-Erkennung relevant ist. Eine spätere
Aufbewahrungsrichtlinie muss mit den buchhalterischen und datenschutzrechtlichen
Pflichten abgestimmt werden.

Administratoren sehen fehlgeschlagene Ereignisse unter Einstellungen und
können ausschließlich `failed_retryable` erneut verarbeiten. Erfolgreiche oder
terminale Ereignisse können nicht über diesen Weg erneut ausgelöst werden.

## Staging-Checkliste

1. Stripe-Testkonto bzw. Testmodus verwenden.
2. Test-API-Key als `STRIPE_API_KEY` setzen.
3. Staging-Webhook anlegen und dessen Signing Secret als
   `STRIPE_WEBHOOK_SECRET` setzen.
4. `APP_URL` auf die HTTPS-Adresse des Staging-Frontends setzen.
5. Migration 10 zuerst als Dry Run und danach ausschließlich gegen die
   freigegebene Staging-Datenbank ausführen.
6. Backend und Frontend mit demselben freigegebenen Git-Stand deployen.
7. Fehlende/ungültige Signatur, ein signiertes Testereignis und doppelte
   Zustellung prüfen. Keine echte Karte und keinen Live-Modus verwenden.
8. Healthcheck, Rollen, B2C-Gastzugriff und Admin-Ereignisansicht prüfen.

## Recovery und bekannte Grenzen

- Ein Absturz nach Stripe-Erstellung, aber vor lokaler Session-Speicherung,
  wird über dieselbe Stripe-Idempotency-Key wiederaufgenommen.
- Ein Absturz nach wirtschaftlicher Verbuchung, aber vor Ledger-Abschluss,
  kann erneut zugestellt werden; CAS-Filter und Payment-Snapshot verhindern die
  zweite Verbuchung.
- Die bestehende Shop-Zahlungsbestätigung wird höchstens einmal beansprucht und
  versendet. Ein Fehler wird am Auftrag markiert; eine umfassende, garantierte
  E-Mail-Outbox ist ein separates Folgepaket.
- Die Browser-Rückkehr fragt nur den autorisierten ORDO-Status ab und pollt
  begrenzt. Sie ist kein Zahlungsnachweis.
- Vollständige Refund-, Dispute- und E-Mail-Outbox-Prozesse sind nicht
  Bestandteil dieses Pakets.
