# Tenant-Isolation der ORDO-Business-Domänen

## Geltungsbereich

Arbeitspaket 3.4 bindet die Collections `companies`, `products`, `customer_prices`, `price_history` und `uploads` an einen serverseitig aufgelösten `TenantContext`. Arbeitspaket 3.5 ergänzt `offers`, `orders`, `invoices`, `contracts` und `subscriptions`. Arbeitspaket 3.6 ergänzt `machines`, `machine_requests`, `shop_orders`, `newsletter`, `settings` und `push_registrations`. Die Zugriffsschicht ergänzt bei jedem Read, Update und Delete automatisch `tenantId`. Bei Inserts und Upserts setzt sie `tenantId` serverseitig.

Der aktuelle Übergangsbetrieb benötigt ausdrücklich:

- `TENANCY_MODE=single`
- `DEFAULT_TENANT_ID=tnt_ss_0001`

Der konfigurierte Tenant muss als genau ein aktives kanonisches Tenant-Dokument vorhanden sein. Fehlende, ungültige oder widersprüchliche Konfiguration wird ohne Default-Tenant abgewiesen.

## Clientdaten

Tenant-Angaben aus Request Body, Query, Path, Header oder Client-State wählen keinen Tenant aus. Request-Modelle reichen ein zusätzliches `tenantId` nicht an die Persistence-Schicht weiter. Ein widersprüchliches `tenantId`, das eine interne aufrufende Stelle dennoch an die Zugriffsschicht übergibt, führt zu einem Fehler. `tenantId` kann durch Updates weder gesetzt, entfernt noch umbenannt werden.

API-Antworten entfernen das interne Feld `tenantId`. Cross-Tenant-Dokumente werden durch den Datenbankfilter nicht gefunden. Fachliche Berechtigungsprüfungen innerhalb desselben Tenants bleiben bestehen.

## Legacy-Daten

Dokumente ohne `tenantId` werden von den umgestellten Zugriffen nicht gefunden und niemals automatisch `tnt_ss_0001` zugeordnet. Dadurch sind bestehende Legacy-Dokumente dieser Collections nach Aktivierung der Codepfade nicht verfügbar, bis ein kontrollierter Backfill separat geprüft, freigegeben und ausgeführt wurde.

Dieses Arbeitspaket enthält keinen Backfill und keine Migration.

Die bisherige Startindex-Konfiguration enthält weiterhin den global eindeutigen
Produktindex `uniq_product_id`. In der Expand-Phase verhindert dieser Index in
einer realen Datenbank noch gleiche Produkt-IDs in zwei Tenants, obwohl die neue
Zugriffsschicht solche Dokumente korrekt unterscheiden kann. Der alte Index darf
erst in einer separat freigegebenen Contract-Migration entfernt werden.

## Referenzen und Pricing

Vor dem Schreiben eines Kundenpreises werden Company und Product innerhalb desselben Tenant-Kontexts geladen. Fehlt eine Referenz oder liegt sie in einem anderen Tenant, wird derselbe allgemeine Nicht-gefunden-Fehler zurückgegeben. `customer_prices` und die dazu erzeugten `price_history`-Einträge erhalten denselben Tenant.

Die bestehende Preisreihenfolge und sämtliche Preiswerte bleiben unverändert. Insbesondere bleibt ein vorhandener fester Kundenpreis vor automatischen Mengenstaffeln maßgeblich.

Die Referenzprüfung und der anschließende Write sind ohne MongoDB-Transaktion nicht atomar. Eine gleichzeitig gelöschte Company oder ein gleichzeitig gelöschtes Product kann deshalb theoretisch zwischen Prüfung und Kundenpreis-Write verschwinden. Das muss vor vollständiger Multi-Tenant-Nutzung zusammen mit den übrigen transaktionalen Domänen bewertet werden.

Auch Price-History-Insert und Customer-Price-Upsert bilden weiterhin keine
atomare Einheit. Zwei gleichzeitige Preisänderungen können daher beide denselben
alten Preis protokollieren oder nach einem Teilfehler History und aktuellen
Preis auseinanderlaufen lassen. Die Tenant-Filter verhindern dabei eine
tenantübergreifende Mutation, lösen aber bewusst noch nicht die fachliche
Versions- und Transaktionsfrage des späteren Pricing-Arbeitspakets.

## Upload-Grenzen

Neue Upload-Metadaten erhalten `tenantId`; der Abruf sucht `storagePath` nur im aktuellen Tenant. Metadaten ohne `tenantId` werden nicht gefunden.

Historische Dateien, die vor Einführung der `uploads`-Metadaten gespeichert
wurden, besitzen möglicherweise noch gar keinen Metadatensatz. Solche Dateien
sind über den neuen Abrufpfad ebenfalls nicht sichtbar. Ein späterer,
kontrollierter Backfill muss deshalb nicht nur vorhandene Metadaten, sondern
auch die tatsächlich referenzierten physischen Dateien inventarisieren.

Die physischen Storage-Keys, vorhandenen Dateien und Object-Storage-Architektur werden in diesem Paket nicht verändert. Es werden keine Objekte verschoben und keine neue Bucket- oder Sichtbarkeitsstrategie eingeführt. Weil physische Keys noch nicht technisch pro Tenant getrennt sind, bleibt die Storage-Key-Isolation Bestandteil des späteren Storage-Arbeitspakets.

Der physische Upload erfolgt weiterhin vor dem Schreiben der Metadaten. Scheitert
der Metadaten-Write danach, kann deshalb ein nicht mehr referenziertes
Storage-Objekt zurückbleiben. Eine transaktionale oder kompensierende Bereinigung
gehört zum späteren Storage-Arbeitspaket; sie wird hier nicht durch einen neuen
Storage-Mechanismus vorgezogen.

## Operative Randdomänen

Maschinen und Maschinenanfragen werden einschließlich ihrer tatsächlich vorhandenen Referenzen auf Company, Product und Contract im aktuellen Tenant aufgelöst. Fremde oder fehlende Referenzen werden gleich behandelt. Die Prüfung und ein anschließender Write bilden weiterhin keine MongoDB-Transaktion; die allgemeine Race- und Idempotenzabsicherung folgt in einem späteren Arbeitspaket.

Shop-Bestellungen verwenden für Katalogprodukt, Bestellung, Checkout-Referenz und Statusänderung denselben serverseitigen Tenant-Kontext. Diese Umstellung ändert weder B2C-Preise noch Stripe-Zahlungslogik. Globale Legacy-Shopbestellungen bleiben unsichtbar.

Shop-Settings verwenden das Zielmodell `(tenantId, key)` mit dem Schlüssel `shop`. Ein globales Legacy-Dokument mit `_id = shop` wird nicht übernommen oder automatisch S&S zugeordnet. Solange kein tenantgebundenes Dokument geschrieben oder kontrolliert migriert wurde, liefert die Anwendung ausschließlich ihre bestehenden sicheren Laufzeit-Standardwerte und erzeugt beim Lesen kein Dokument.

Newsletter-E-Mail, Bestätigungs-, Abmelde- und Rabattcode-Lookups sind tenantgebunden. Push-Registrierungen speichern zusätzlich eine serverseitig gebildete, tenantpräfixierte Provider-ID. Vorhandene Provider-Registrierungen ohne diese ID müssen sich nach einem kontrollierten Rollout erneut registrieren; es gibt keinen globalen Fallback.

## Audit-Isolation

`audit_log` verwendet dieselbe serverseitige Tenant-Zugriffsschicht wie die
Business-Collections. Tenantbezogene Ereignisse können nur über eine
`TenantBusinessAccess`-Instanz geschrieben werden; `tenantId` wird dabei aus
dem bereits aufgelösten `TenantContext` gesetzt. Der Tenant-Admin-Endpunkt
filtert vor Sortierung und 200er-Limit auf diesen Tenant und entfernt
`tenantId` aus der Antwort.

Globale Identity-Ereignisse (`login`, `user.create`, `user.reset`) bleiben
ausdrücklich global. Sie besitzen keine `tenantId` und werden deshalb im
Tenant-Admin-Endpunkt nicht angezeigt. Das gilt auch für historische
Audit-Einträge ohne `tenantId`. Es gibt keinen S&S-Legacy-Fallback.

## Noch nicht vollständig umgestellte Bereiche

`users` bleibt entsprechend dem Zielmodell eine globale Identitäts-Collection.
Tenant-Memberships, tenantbezogene Rollen und tenantbezogene Benutzerlisten
folgen erst im dafür freigegebenen Arbeitspaket. Die Tenant-Isolation der
Audit-Persistenz ersetzt diese noch fehlende Benutzerautorisierung nicht.

## Verbindliche Sicherheitsregel für spätere KI-Funktionen

Interne kaufmännische Daten wie Einkaufspreise, Deckungsbeiträge, Margen,
Kunden- oder Maschinenprofitabilität, interne Kosten, Provisionen und sensible
unternehmensweite Kennzahlen dürfen ausschließlich für eine ausdrücklich dafür
autorisierte Admin-Rolle freigegeben werden. B2B-Kunden, B2C-Kunden und
Vertriebspartner dürfen technisch keinen Zugriff auf diese Daten erhalten.

Diese Grenze muss serverseitig vor jeder Übergabe an ein KI-Modell durchgesetzt
werden. Ein Prompt oder eine Anweisung an das Modell ist keine
Berechtigungsprüfung. Nicht autorisierte Daten dürfen das Modell gar nicht erst
erreichen.
