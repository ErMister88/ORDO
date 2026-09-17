# ORDO Staging-Datenbestand

Stand der Bestandsaufnahme: 18. September 2026

Arbeitspaket: 0 – Staging-Bestand und Wiederherstellbarkeit sichern

## Zweck und Grenzen

Diese Dokumentation hält den lesend ermittelten Zustand der ORDO-Staging-Datenbank fest. Während der Bestandsaufnahme wurden keine Geschäftsdaten, Collections, Indizes oder Konfigurationen verändert.

Die Dokumentation enthält keine Passwörter, Tokens, Connection Strings oder sonstigen Zugangsdaten.

## Umgebung

| Merkmal | Wert |
| --- | --- |
| Render-Service | `ordo-staging-api` |
| Render-Umgebungsbezeichnung | `Production` |
| MongoDB-Datenbank | `ordo_staging` |
| Datenbankanbieter | MongoDB Atlas |
| Zum Prüfzeitpunkt deployter Commit | `e3650ca515b3b5eec556a3a005d7a639f5f1ceb1` |
| Verbindliche Entwicklungsbasis auf GitHub `main` | `c4029819d6821f1c4bb12c68a8c34c6062ded53f` |

Der laufende Staging-Service und GitHub `main` befinden sich damit auf unterschiedlichen Commits. Diese Abweichung ist in `staging-known-risks.md` festgehalten. Im Rahmen von Arbeitspaket 0 wurde kein Deployment ausgelöst.

## Collections und Dokumentzahlen

| Collection | Dokumente |
| --- | ---: |
| `companies` | 4 |
| `contracts` | 2 |
| `counters` | 4 |
| `customer_prices` | 5 |
| `invoices` | 4 |
| `offers` | 2 |
| `orders` | 25 |
| `password_resets` | 0 |
| `products` | 4 |
| `users` | 3 |
| **Gesamt** | **53** |

Collections für `shop_orders`, `subscriptions`, `uploads`, `audit_log`, `machines`, `machine_requests`, `newsletter`, `settings` und `push_registrations` waren zum Prüfzeitpunkt nicht vorhanden.

## Indizes

Jede vorhandene Collection besitzt den automatisch angelegten MongoDB-Index `_id_`.

### Eindeutige fachliche Indizes

| Collection | Indexname | Schlüssel |
| --- | --- | --- |
| `offers` | `uniq_offer_id` | `id` |
| `orders` | `uniq_order_id` | `id` |
| `products` | `uniq_product_id` | `id` |
| `users` | `uniq_email` | `email` |

### TTL-Index

| Collection | Indexname | Schlüssel | Ablauf |
| --- | --- | --- | ---: |
| `password_resets` | `ttl_reset` | `expiresAt` | `expireAfterSeconds: 0` |

### Nicht durch eindeutige Indizes abgesicherte Fachschlüssel

Für folgende Fachschlüssel bestand zum Prüfzeitpunkt kein Unique Constraint:

- `companies.id`
- `contracts.id`
- `invoices.id`
- `users.id`
- Kombination `customer_prices.companyId + productId`

Es wurden in Arbeitspaket 0 keine Indizes ergänzt oder verändert.

## Prüfung der Datenintegrität

### Fehlende fachliche IDs

In `companies`, `contracts`, `invoices`, `offers`, `orders`, `products` und `users` wurden keine Dokumente mit fehlender, leerer oder auf `null` gesetzter fachlicher `id` gefunden. In `customer_prices` fehlten weder `companyId` noch `productId`.

### Doppelte fachliche IDs

Es wurden keine doppelten fachlichen IDs gefunden. Das gilt auch für `users.email` sowie für die Kombination `customer_prices.companyId + productId`.

### Offensichtliche Referenzen

Folgende Beziehungen wurden lesend auf verwaiste Referenzen geprüft:

- Benutzer zu Unternehmen und Vertrieblern
- Unternehmen zu zuständigen Vertrieblern
- Kundenpreise zu Unternehmen und Produkten
- Verträge zu Unternehmen und Produkten
- Angebote zu Unternehmen, Erstellern und Produkten
- Bestellungen zu Unternehmen, Erstellern und Produkten
- Rechnungen zu Unternehmen und, falls vorhanden, Bestellungen
- Passwort-Resets zu Benutzern

Bei diesen Prüfungen wurden keine verwaisten Referenzen gefunden. MongoDB erzwingt diese Beziehungen derzeit nicht als Fremdschlüssel; das Ergebnis beschreibt daher den aktuellen Datenzustand und keine dauerhafte Datenbankgarantie.

## Erkannte Seed- und Demodaten

Die vorhandenen Datensätze stimmen auf Ebene der bekannten Fach-IDs und Dokumentzahlen vollständig mit den im Backend definierten Seed-Daten überein:

| Bereich | Erkannte Seed-Datensätze | Gesamt |
| --- | ---: | ---: |
| Benutzer | 3 | 3 |
| Unternehmen | 4 | 4 |
| Produkte | 4 | 4 |
| Kundenpreise | 5 | 5 |
| Angebote | 2 | 2 |
| Bestellungen | 25 | 25 |
| Verträge | 2 | 2 |
| Rechnungen | 4 | 4 |

Die Prüfung hat keine Daten entfernt oder verändert. Vor einer späteren Bereinigung muss erneut bestätigt werden, dass keine echten Staging-Daten hinzugekommen sind.

## Object Storage

Eine Collection `uploads` war nicht vorhanden. Damit waren keine über MongoDB referenzierten Uploads oder Produktdateien nachweisbar. Inhalt, Versionierung und Backupstatus eines möglicherweise außerhalb der Datenbank vorhandenen Object Storage konnten mangels Zugriff auf ein zugehöriges Storage-Konto nicht geprüft werden.
