# ORDO Demo-Seeding

## Grundsatz

Der normale FastAPI-/Uvicorn-Start erzeugt keine Demo- oder Geschäftsdokumente. Er prüft die Datenbankverbindung, stellt die bereits zuvor verwendeten Betriebsindizes sicher und initialisiert den konfigurierten Dateispeicher.

Demo-Daten werden ausschließlich über `backend/scripts/seed_demo.py` angelegt. Das Script wird weder vom Application-Startup noch von einem API-Endpunkt aufgerufen. Auch der Maschinenkatalog erzeugt beim Lesen keine Einträge mehr.

Die bestehende Variable `ENABLE_DEMO_SEED` ist absichtlich wirkungslos. Sie kann weder Startup-Seeding aktivieren noch den Production-Schutz des Scripts umgehen.

## Erforderliche Konfiguration

- `APP_ENV`: `development`, `dev`, `test`, `testing`, `stage` oder `staging`
- `DB_NAME`: ausdrücklich gewählte Zieldatenbank
- `MONGO_URL`: nur als Secret bereitstellen
- `SEED_ADMIN_PASSWORD`
- `SEED_SALES_PASSWORD`
- `SEED_CUSTOMER_PASSWORD`

Alle drei Passwörter sind erforderlich, weil der vollständige Demo-Datensatz je einen getrennten Admin-, Vertriebs- und Kundenbenutzer anlegt. Es gibt keine Standardpasswörter. Die Werte werden ausschließlich gehasht gespeichert und weder im Ergebnis noch in Logs ausgegeben. Production-Bezeichnungen `prod`, `production` und `live` sind immer gesperrt.

## Expliziter Aufruf

Vom Verzeichnis `backend`:

```bash
python scripts/seed_demo.py --confirm-target <APP_ENV>:<DB_NAME>
```

Beispiel ausschließlich für eine lokale Testdatenbank:

```bash
APP_ENV=test \
DB_NAME=ordo_local_demo \
python scripts/seed_demo.py --confirm-target test:ordo_local_demo
```

Die benötigten übrigen Variablen müssen dabei bereits sicher in der lokalen Umgebung gesetzt sein. Zielbestätigung, `APP_ENV` und `DB_NAME` müssen exakt übereinstimmen. Fehlende oder unbekannte Umgebungen werden vor dem Aufbau einer Datenbankverbindung abgewiesen.

## Verhalten bei vorhandenen Daten

Vor jedem Lauf muss Migration 2 bereits den aktiven, unveränderten S&S-Tenant `tnt_ss_0001` angelegt haben. Der Seed führt keine Migration aus und legt keinen Tenant an. Fehlt der Tenant oder weicht sein kanonisches Dokument ab, endet der Lauf vor dem ersten Business-Write.

Jedes erzeugte Dokument besitzt eine technische Demo-Markierung, eine deterministische MongoDB-`_id` und einen kanonischen SHA-256-Integritätsfingerabdruck. Die tenantgebundenen Collections `companies`, `products`, `customer_prices`, `offers`, `orders`, `contracts`, `invoices` und `machines` erhalten `tenantId = tnt_ss_0001`. `users` und `counters` bleiben bewusst global und erhalten kein `tenantId`.

- Eine Wiederholung fügt vorhandene Demo-Dokumente nicht erneut ein.
- Bei einer Wiederholung müssen die gespeicherten Demo-Benutzer weiterhin zu den ausdrücklich übergebenen `SEED_*_PASSWORD`-Werten passen. Eine Abweichung wird als Konflikt behandelt; der Seed rotiert oder überschreibt Passwörter nicht.
- Spätere Änderungen, fehlende Felder oder ein beschädigter Fingerabdruck werden als Konflikt erkannt. Das Script setzt solche Dokumente niemals eigenständig zurück.
- Der gespeicherte Inhalt wird zusätzlich gegen das feste Manifest der Seed-Version geprüft. Ein neu berechneter Fingerabdruck legitimiert deshalb kein verändertes Dokument.
- Alte `ordo-demo-v1`-Dokumente ohne `tenantId` werden nicht aktualisiert oder neu markiert. Sie blockieren den tenant-aware V2-Seed bis zu einer ausdrücklich freigegebenen Upgrade- oder Bereinigungsstrategie.
- Nicht kollidierende fremde Dokumente bleiben unverändert.
- Verwendet ein nicht markiertes Dokument bereits eine reservierte Demo-ID, E-Mail-Adresse oder Kundenpreis-Kombination, bricht der vollständige Preflight vor dem ersten Schreibvorgang ab.
- Es werden keine Collections geleert, gelöscht oder zurückgesetzt.

Ein technischer Fehler während der Ausführung führt zu einem Exit-Code ungleich null und niemals zu einer Erfolgsmeldung. Da mehrere Collections betroffen sind, kann ein Infrastrukturfehler einen teilweise eingefügten Stand hinterlassen. Alle Dokumente sind einzeln wiederholbar; derselbe explizite Befehl kann nach Behebung des Fehlers sicher erneut ausgeführt werden.

Der Lauf ist für eine dedizierte Demo-/Testdatenbank ohne gleichzeitige fachliche Schreibzugriffe vorgesehen. Parallele identische Seed-Läufe sind durch deterministische `_id`-Werte abgesichert. Nach jedem Insert wird die fachliche Identität erneut geprüft; ein konkurrierender fremder Schreibvorgang führt damit zu einem fehlgeschlagenen Lauf statt zu einer falschen Erfolgsmeldung. Bei Collections ohne eindeutigen fachlichen Index können in diesem Race-Fall vorübergehend das fremde und das markierte Demo-Dokument nebeneinander stehen. Eine Wiederholung bleibt fail-safe und schreibt nicht weiter, bis der Konflikt bewusst geklärt wurde. Deshalb darf eine Demo-Datenbank während des Seed-Laufs nicht anderweitig beschrieben werden.

## Tests und Staging

Integrationstests, die Demo-Benutzer, Produkte, Bestellungen oder Maschinen voraussetzen, müssen zuerst Migration 2 gegen eine ausdrücklich gewählte isolierte Testdatenbank ausführen und danach dieses Script explizit starten. Der normale Backend-Start übernimmt weder Migration noch Seed.

`ordo_staging` wurde in Arbeitspaket 2 weder gelesen noch verändert. Ein späterer Seed-Lauf gegen eine Staging-Datenbank benötigt eine gesonderte ausdrückliche Freigabe und weiterhin die exakte Zielbestätigung. Production bleibt unabhängig von jeder Bestätigung gesperrt.
