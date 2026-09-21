# Tenant-Isolation für Companies, Products, Pricing und Upload-Metadaten

## Geltungsbereich

Arbeitspaket 3.4 bindet die Collections `companies`, `products`, `customer_prices`, `price_history` und `uploads` an einen serverseitig aufgelösten `TenantContext`. Die Zugriffsschicht ergänzt bei jedem Read, Update und Delete automatisch `tenantId`. Bei Inserts und Upserts setzt sie `tenantId` serverseitig.

Der aktuelle Übergangsbetrieb benötigt ausdrücklich:

- `TENANCY_MODE=single`
- `DEFAULT_TENANT_ID=tnt_ss_0001`

Der konfigurierte Tenant muss als genau ein aktives kanonisches Tenant-Dokument vorhanden sein. Fehlende, ungültige oder widersprüchliche Konfiguration wird ohne Default-Tenant abgewiesen.

## Clientdaten

Tenant-Angaben aus Request Body, Query, Path, Header oder Client-State wählen keinen Tenant aus. Request-Modelle reichen ein zusätzliches `tenantId` nicht an die Persistence-Schicht weiter. Ein widersprüchliches `tenantId`, das eine interne aufrufende Stelle dennoch an die Zugriffsschicht übergibt, führt zu einem Fehler. `tenantId` kann durch Updates weder gesetzt, entfernt noch umbenannt werden.

API-Antworten entfernen das interne Feld `tenantId`. Cross-Tenant-Dokumente werden durch den Datenbankfilter nicht gefunden. Fachliche Berechtigungsprüfungen innerhalb desselben Tenants bleiben bestehen.

## Legacy-Daten

Dokumente ohne `tenantId` werden von den umgestellten Zugriffen nicht gefunden und niemals automatisch `tnt_ss_0001` zugeordnet. Dadurch sind bestehende Legacy-Unternehmen, Produkte, Kundenpreise, Preisverläufe und Upload-Metadaten nach Aktivierung dieser Codepfade nicht verfügbar, bis ein kontrollierter Backfill separat geprüft, freigegeben und ausgeführt wurde.

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

## Noch nicht umgestellte Domänen

Orders, Offers, Invoices, Contracts, Machines, Subscriptions und Shop Orders bleiben außerhalb dieses Arbeitspakets. Wo diese Domänen Companies, Products oder Customer Prices nachschlagen, verwenden diese Lookups bereits die tenantgebundene Zugriffsschicht. Die eigenen Dokumente und Abfragen dieser Domänen erhalten in 3.4 jedoch noch keinen vollständigen Tenant-Scope.

Insbesondere dürfen gleiche Company-IDs in verschiedenen Tenants erst dann systemweit verwendet werden, wenn auch die referenzierenden langlebigen Domänen vollständig tenantgebunden sind. Das ist kein sicherer Zustand für einen echten Multi-Tenant-Produktionsbetrieb und muss in den folgenden Arbeitspaketen geschlossen werden.

Der Vertragspreis-Lookup beim B2B-Bestellaufbau liest weiterhin direkt aus der
noch nicht vollständig umgestellten `contracts`-Collection, verlangt an dieser
einzelnen Zugriffskante aber fail-closed `tenantId` aus dem serverseitigen
`TenantContext`. Fremde und Legacy-Contracts ohne `tenantId` beeinflussen damit
keinen Preis. Contract-Writes, weitere Contract-Router und die Preisreihenfolge
bleiben unverändert; die vollständige Contract-Isolation folgt in einem späteren
Arbeitspaket.

`users` bleibt entsprechend dem Zielmodell eine globale Identitäts-Collection.
Tenant-Memberships und tenantbezogene Benutzerlisten folgen erst im dafür
freigegebenen Arbeitspaket. Ebenso enthalten bestehende Audit-Einträge noch
keine durchgängige `tenantId`. Benutzerverwaltung und Audit-Auswertung dürfen
daher vor dieser Erweiterung nicht für mehrere aktive Tenants freigeschaltet
werden.
