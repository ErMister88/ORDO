# Tenant-Memberships und globale Identitäten

## Zielmodell

`users` enthält die globale Benutzeridentität. Ein Benutzer wird nicht durch
`users.tenantId` an einen Tenant gebunden. Die Collection
`tenant_memberships` verbindet eine globale Identität mit einem Tenant und
enthält die tenantbezogene Rolle, den Status sowie bei B2B-Kunden die
zugeordnete Company.

Eine Membership enthält:

- `id`: stabile technische Membership-ID
- `tenantId`: technischer Tenant-Schlüssel
- `userId`: Referenz auf die globale Identität
- `role`: `admin`, `sales` oder `customer`
- `status`: `active` oder `inactive`
- `companyId`: nur für `customer`, sonst `null`
- `createdAt` und `updatedAt`

`users.role`, `users.companyId` und `users.salesRepId` bleiben während der
Kompatibilitätsphase gespeichert. Sie erteilen keinen Tenant-Zugriff. Für jede
authentifizierte Business-Anfrage werden Membership und Tenant serverseitig
erneut gelesen und validiert.

Die einzige bewusst tenantübergreifende Membership-Abfrage beginnt mit der
serverseitig authentifizierten globalen User-ID. Sie lädt ausschließlich die
Memberships dieser Identität, damit der Server eine eindeutige oder ausdrücklich
angeforderte Membership validieren kann. Alle tenantgebundenen Membership-
Verwaltungszugriffe laufen anschließend über die normale Tenant-Zugriffsschicht.

## Tenant-Auswahl und Autorisierung

Bei genau einer aktiven Membership ist der Tenant eindeutig. Bei mehreren
Memberships muss der Login einen gewünschten Tenant angeben. Diese Angabe ist
nur eine Auswahl: Der Server akzeptiert sie ausschließlich, wenn eine aktive
Membership derselben globalen Identität für diesen aktiven Tenant existiert.
Das ausgestellte JWT enthält Tenant- und Membership-ID als Hinweis; beide
werden bei jeder Anfrage gegen die Datenbank geprüft. Rolle und Company stammen
immer aus der validierten Membership.

Ohne Membership, bei einer inaktiven Membership, bei einem inaktiven Tenant
oder bei widersprüchlichen bzw. mehrdeutigen Daten wird der Zugriff abgewiesen.
Es gibt keinen Fallback auf `DEFAULT_TENANT_ID`, S&S oder globale User-Felder.

Öffentliche und anonyme B2C-Shop-Zugriffe besitzen keine Membership. Sie nutzen
weiterhin die ausdrücklich konfigurierte, fail-closed Public-Tenant-Auflösung.
Eine Domain- oder Slug-basierte öffentliche Tenant-Auswahl ist noch nicht
festgelegt. Shop-Identity- und Token-Härtung bleiben einem späteren Paket
vorbehalten.

## Indizes und Migration

Migration 3 erweitert das Schema additiv um `tenant_memberships` und bereitet
folgende Indizes vor:

- eindeutige Membership-ID
- eindeutige Kombination aus `tenantId` und `userId`
- Tenant-, Status- und Rollen-Lookup
- User-, Status- und Tenant-Lookup

Die Migration erzeugt für erkannte S&S-Legacy-User der Rollen `admin`, `sales`
und `customer` deterministische Memberships. `shopuser` wird nicht in das
interne Rollenmodell übernommen. Kunden-Memberships werden nur vorbereitet,
wenn ihre Company bereits eindeutig dem S&S-Tenant zugeordnet ist. Bestehende
globale User-Dokumente werden nicht verändert.

Die Migration wurde ausschließlich gegen isolierte In-Memory-Testdatenbanken
getestet. Sie wurde weder gegen `ordo_staging` noch gegen eine andere echte
Datenbank ausgeführt. Vor einem realen Lauf sind Dry Run, Backup-Nachweis,
Review des Plans und eine gesonderte Freigabe erforderlich.

## Demo-Daten und Rollout

Neue Demo-Daten verwenden Seed-Version `ordo-demo-v4` und enthalten
Memberships für die internen Demo-Identitäten. Bestehende Demo-Daten mit einem
älteren Fingerprint werden nicht stillschweigend überschrieben. Ein Upgrade
solcher Daten braucht einen kontrollierten, ausdrücklich freigegebenen Ablauf.

Die Anwendung arbeitet nach Aktivierung der Membership-Autorisierung
fail-closed: bestehende interne Benutzer ohne Membership können sich nicht in
den Business-Bereich einloggen. Deshalb muss die vorbereitete Migration vor
dem Rollout der neuen Anwendung in der jeweiligen Umgebung kontrolliert
erfolgreich abgeschlossen sein.

## Verbleibende Betriebsgrenzen

- Benutzeranlage schreibt globale Identität und Membership in getrennte
  Collections. Bei einem Membership-Fehler entfernt der Code die von derselben
  Anfrage angelegte Identität und gegebenenfalls neue Company kompensierend.
  MongoDB-Transaktionen für allgemeine atomare Workflows folgen später.
- Ein globales Passwort betrifft alle Tenant-Memberships derselben Identität.
  Der aktuelle Tenant-Admin-Reset weist Identitäten mit mehreren aktiven
  Memberships deshalb ab. Ein plattformweiter Administrationsprozess ist noch
  nicht definiert.
- Membership-Änderungs- und Einladungsworkflows sind nicht Bestandteil dieses
  Pakets.
- JWT-Invalidierung, erzwungener Passwortwechsel und gemeinsame Login-Limits
  sind in `auth-session-security.md` beschrieben.
