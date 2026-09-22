# Authentifizierung und Session-Sicherheit

## Tokenprofile

ORDO verwendet weiterhin signierte JWT Access Tokens. Interne Tenant-Sitzungen
und B2C-Shop-Sitzungen besitzen getrennte, serverseitig festgelegte Profile:

| Profil | `token_type` | Audience | Scope |
| --- | --- | --- | --- |
| Tenant | `tenant` | `ordo-tenant` | `tenant:access` |
| Shop | `shop` | `ordo-shop` | `shop:access` |

Beide Profile verlangen den Issuer `ordo`, Subject, Ausstellungs- und
Ablaufzeit sowie eine nichtnegative `auth_version`. Tenant-Tokens enthalten
zusätzlich Tenant- und Membership-ID. Rolle und Tenantdaten im Token sind nur
Hinweise. Das Backend lädt bei jeder internen Anfrage Identität, Membership und
Tenant erneut und verwendet ausschließlich deren aktuellen Zustand.

Ein Shop-Token kann keinen internen Tenant-Endpunkt authentifizieren. Ein
gültiges Tenant-Token darf den öffentlichen B2C-Shop verwenden; dabei entstehen
keine B2B-Preis- oder Tenantrechte. Ein vorhandener ungültiger Token wird bei
optionalen Shop-Zugriffen abgewiesen und niemals als Gast behandelt. Gastzugriff
ist nur ohne Authorization-Header möglich.

## Session-Invalidierung

Globale Identitäten besitzen `authVersion`. Für bestehende Identitäten ohne
dieses Feld gilt ausschließlich die kompatible Version `0`. Passwortänderung,
Passwort-Recovery und administrativer Passwortreset ändern Passwortzustand und
Version in genau einem atomaren MongoDB-Update. Dadurch werden alle zuvor
ausgestellten Tokens dieser Identität sofort ungültig.

Eine deaktivierte Identität wird ebenfalls bei jeder Anfrage abgewiesen.
Membership-Deaktivierung, Rollenänderung, Entfernung oder Tenant-Deaktivierung
wirken sofort, weil diese Werte nicht aus dem JWT autorisiert werden.

Es ist kein Daten-Backfill für `authVersion` erforderlich. Neue Identitäten und
Demo-Identitäten schreiben Version `0` ausdrücklich. Der Demo-Seed wurde wegen
dieser geänderten Sicherheitsdaten auf `ordo-demo-v4` angehoben. Bestehende
Demo-Daten werden nicht automatisch verändert.

## Erzwungener Passwortwechsel

Bei `must_change_password=true` bleiben Login, `/auth/me` und der authentisierte
Passwortwechsel erreichbar. Alle normalen Tenant-Endpunkte und die Nutzung des
B2C-Shops mit dieser Identität werden zentral blockiert. Nach erfolgreichem
Wechsel wird der Marker im selben atomaren Update entfernt, die Session-Version
erhöht und das Frontend zur erneuten Anmeldung geführt.

## Schutz vor Brute Force

Authentifizierungslimits liegen in der MongoDB-Collection
`auth_rate_limits`. Feste Zeitfenster und atomare Zähler gelten damit gemeinsam
für alle Backend-Worker. Rohwerte für E-Mail, User-ID und IP-Adresse werden
nicht gespeichert; der technische Schlüssel ist ein mit dem serverseitigen
Secret gebildeter HMAC-SHA-256 aus Gruppe, Schlüsselart, normalisiertem Wert
und Zeitfenster. Ein TTL-Index entfernt
abgelaufene Buckets.

`/auth/login` und `/shop/login` verwenden dieselbe Gruppe
`credential_login`, dasselbe normalisierte Accountmerkmal und dieselben Limits.
Ein Wechsel zwischen diesen Endpunkten oder Änderungen von Großschreibung und
Whitespace setzen den Zähler daher nicht zurück. Recovery-Anfrage,
Recovery-Code-Prüfung, Passwortänderung, Shop-Registrierung und administrative
Passwortresets haben getrennte, ihrer Funktion entsprechende Limits.

Der Anwendungscode vertraut keine frei eingesandten Proxy-Header. Im Betrieb
muss der Hosting-Proxy so konfiguriert sein, dass FastAPI in `request.client`
die verifizierte Clientadresse erhält. Fehler des gemeinsamen Limit-Stores
werden nicht als Freigabe behandelt.

## Betrieb und Rollout

- `JWT_SECRET` muss in jeder Umgebung ein starkes, ausschließlich serverseitiges
  Secret sein. Staging und Production starten mit weniger als 32 Byte nicht.
  ORDO akzeptiert ausschließlich `JWT_ALGORITHM=HS256` und eine positive
  `ACCESS_TOKEN_MINUTES`-Konfiguration. Ein Secret-Wechsel invalidiert alle
  vorhandenen Tokens.
- Der Startprozess erzeugt nur den TTL-Index für Auth-Limits. Er erzeugt keine
  Benutzer- oder Demo-Daten.
- Migration 3 wird durch dieses Paket weder ausgeführt noch verändert.
- Vor dem Rollout auf eine Umgebung mit alten internen Benutzern muss weiterhin
  der freigegebene Membership-Migrationsablauf eingehalten werden.
- Frontend und Shop löschen bei einer 401-Antwort Token, laufende Queries und
  benutzerspezifischen React-Query-Cache.

## Verbleibende Grenzen

- JWTs sind zeitlich begrenzte stateless Access Tokens; eine Liste einzelner Sessions
  oder selektiver Geräte-Logout existiert derzeit nicht.
- Die Zeitfensterberechnung der verteilten Limits setzt synchronisierte
  Serveruhren voraus.
- Tenantübergreifende administrative Passwortresets bleiben absichtlich
  gesperrt, bis ein plattformweiter Administrationsprozess definiert ist.
