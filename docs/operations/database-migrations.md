# ORDO MongoDB-Migrationen

## Zweck

Das Migrationssystem verwaltet technische Schemaänderungen unabhängig vom FastAPI-Startup. Migrationen werden ausschließlich über `backend/scripts/migrate.py` gestartet. Der normale Anwendungsstart führt den Runner nicht aus.

Die technische Collection `schema_migrations` speichert für jede Version mindestens:

- `version`
- `name`
- `checksum`
- `status`
- `startedAt`
- `completedAt`
- `applicationVersion`
- `resultSummary`

Zeitwerte werden als BSON UTC Datetimes gespeichert. `schema_migrations.version` besitzt den eindeutigen Index `uniq_schema_migrations_version`.

Lock- und Migrationsmetadaten verwenden explizit MongoDB `majority` Read/Write Concern, damit bestätigte technische Zustände bei einem Replica-Set-Failover nicht stillschweigend auf einen älteren Stand zurückfallen.

## Aufbau

| Pfad | Aufgabe |
| --- | --- |
| `app/migrations/models.py` | Definitionen, Checksummen und Fehlerklassen |
| `app/migrations/registry.py` | Explizite, abhängigkeitsgeordnete Migrationsliste |
| `app/migrations/runner.py` | Planung, Ausführung, Status und Recovery |
| `app/migrations/lock.py` | MongoDB-Lease gegen parallele Runner |
| `app/migrations/versions/` | Unveränderliche versionierte Migrationen |
| `scripts/migrate.py` | Separater Kommandozeilen-Runner |

## Baseline

Migration `1 – baseline_current_schema` nimmt den bestehenden Schema-Stand auf. Sie inventarisiert ausschließlich Collection-Namen und Dokumentzahlen. Sie verändert keine Geschäftsdokumente und legt keine Business-Indizes an.

Geschrieben werden bei einem echten Lauf nur:

- der technische Migrationseintrag,
- der eindeutige Index auf `schema_migrations.version`,
- die technische Lease in `schema_migration_lock`.

## Neue Migration erstellen

1. Unter `app/migrations/versions/` eine neue Datei mit fortlaufender Nummer anlegen, zum Beispiel `v0002_example.py`.
2. Eine `Migration` mit eindeutiger Version und einem Namen in `snake_case` exportieren.
3. Eine vollständig lesende `inspect()`-Funktion bereitstellen. Sie liefert Preconditions und erwartete Änderungen als `MigrationPlan`.
4. `apply(database, context)` idempotent und wiederaufnehmbar implementieren. Das Ergebnis muss eine knappe, nicht sensible Zusammenfassung liefern.
5. Bei längeren, in Batches ausgeführten Änderungen regelmäßig `context.checkpoint()` aufrufen. Der Aufruf bricht ab, wenn der Runner seine Lease verloren hat.
6. Die Migration in `app/migrations/registry.py` registrieren und ihre direkte Abhängigkeit angeben. Die Versionsnummer bleibt die unveränderliche Identität; die geprüfte Abhängigkeitsreihenfolge bestimmt die Ausführung.
7. Nach der ersten Anwendung die Migrationsdatei nicht mehr verändern. Eine Änderung führt absichtlich zu einem Checksum-Fehler; Korrekturen erfolgen über eine neue Version.

Die Checksumme umfasst den vollständigen Dateiinhalt. Unterschiedliche Zeilenenden (`LF`, `CRLF`, `CR`) werden vor dem Hashing auf `LF` normalisiert; Dateipfad und Laufzeitumgebung fließen nicht ein.

Eine Migrationsdatei muss ihre fachliche Transformationslogik selbst enthalten. Änderungen an importierten Hilfsfunktionen fließen nicht in ihre Datei-Checksumme ein und dürfen deshalb nicht unbemerkt das Verhalten einer bereits angewendeten Migration verändern.

Für umfangreiche Änderungen gilt:

`EXPAND → BACKFILL → VERIFY → APPLICATION SWITCH → ENFORCE → später CONTRACT`

Die Phasen sollen auf getrennte, idempotente Migrationen verteilt werden. Das Framework führt keine automatische destruktive Rollback-Magie aus.

## Dry Run

Vom Verzeichnis `backend`:

```bash
python scripts/migrate.py --dry-run
```

Der Dry Run ist gegenüber der Zieldatenbank vollständig read-only. Er kopiert Dokumente und Indexdefinitionen in eine ausschließlich im Prozess gehaltene In-Memory-Sandbox und führt dort die ausstehenden Migrationen in ihrer echten Abhängigkeitsreihenfolge aus. Dadurch sieht Migration N+1 die simulierten Ergebnisse von Migration N, ohne Collections, Indizes, Lock- oder Statusdokumente in der Zieldatenbank anzulegen. Ausgegeben werden:

- `APP_ENV`
- Ziel-Datenbank
- erkannte Migrationen
- bereits angewendete Migrationen
- geplante Migrationen
- Preconditions
- erwartete Änderungen und ermittelbare Dokumentzahlen

Connection String und Secrets werden nicht ausgegeben. Ein Dry Run verwendet bewusst keine Lease; der Zustand kann sich deshalb zwischen Vorschau und späterer Ausführung ändern und wird beim echten Lauf unter der Lease erneut geprüft.

`inspect()` erhält weiterhin eine eingeschränkte Datenbankansicht, die Schreibmethoden blockiert. Aggregationen mit `$out` oder `$merge` werden ebenfalls abgewiesen. `apply()` läuft beim Dry Run nur gegen die In-Memory-Sandbox. Vor und nach der Simulation wird ein BSON-typisierter Fingerprint der Quelldaten und Indizes verglichen; eine parallele Änderung lässt den Dry Run fehlschlagen.

Die Simulation verwendet bewusst dieselben Migrationen und dieselbe Registry wie der echte Lauf. Sie ersetzt keinen abschließenden Test gegen eine isolierte echte MongoDB, verhindert aber den früheren Fehler, spätere Migrationen gegen den unveränderten Ausgangszustand zu prüfen.

Die Sperre schützt gegen versehentliche Schreibzugriffe über die bereitgestellte Python-Schnittstelle. Python-Code im selben Prozess könnte eine solche Laufzeitsperre absichtlich umgehen. Für zusätzliche Absicherung sollte ein Dry Run in CI/CD und Production nach Möglichkeit mit einem MongoDB-Benutzer ausgeführt werden, der ausschließlich Leserechte besitzt.

## Normaler Lauf

```bash
python scripts/migrate.py
```

Erforderliche Konfiguration:

- `APP_ENV`
- `DB_NAME`
- `MONGO_URL`

Optional:

- `APP_VERSION` oder `RENDER_GIT_COMMIT`
- `MIGRATION_LEASE_SECONDS`, Standard 60 Sekunden

Vor jedem Lauf müssen `APP_ENV` und `DB_NAME` in der Ausgabe kontrolliert werden. Die bestehende Datenbank `ordo_staging` darf erst nach gesonderter Freigabe als Ziel eines echten Laufs verwendet werden.

## Production Guard

Dry Runs bleiben in Production erlaubt, weil sie vollständig lesend sind. Ein schreibender Production-Lauf benötigt zwei übereinstimmende Freigaben:

1. Kommandozeilenoption `--allow-production`
2. `MIGRATION_PRODUCTION_APPROVAL=<APP_ENV>:<DB_NAME>`

Beispiel für die Form, ohne reale Zielwerte:

```bash
MIGRATION_PRODUCTION_APPROVAL=production:example_database \
python scripts/migrate.py --allow-production
```

Die zielgebundene Freigabe eignet sich für CI/CD und verhindert, dass eine Freigabe versehentlich für eine andere Datenbank wiederverwendet wird. Sie ist kein Secret.

Unbekannte oder falsch geschriebene `APP_ENV`-Werte werden abgewiesen, damit ein Tippfehler nicht als vermeintlich sichere Nicht-Production-Umgebung behandelt wird.

## Lock und Lease

Der Runner erwirbt atomar das feste Dokument `schema_migration_lock._id = global`. MongoDBs eindeutiger `_id`-Index verhindert zwei gleichzeitige Besitzer.

- Die Lease besitzt eine Ablaufzeit.
- Ein Hintergrund-Heartbeat verlängert sie während des Laufs.
- Ein zweiter Runner bricht ab, solange die Lease aktiv ist.
- Nach Ablauf kann ein neuer Runner atomar übernehmen.
- Updates und Freigabe enthalten immer die Owner-ID; ein alter Runner kann die Lease eines neuen Besitzers nicht freigeben.
- Jede Übernahme erhöht zusätzlich eine Generation als Fencing Token. Statusänderungen einer Migration gelten nur für Owner und Generation des aktiven Laufs.
- Ein verspäteter Heartbeat darf eine bereits abgelaufene Lease nicht wiederbeleben.
- Vor dem Abschlussstatus jeder Migration wird der Besitz erneut geprüft.
- Lange Migrationen können zusätzlich zwischen Batches über `context.checkpoint()` abbrechen, sobald die Lease verloren wurde.

Eine bereits laufende MongoDB-Operation kann nicht durch das Lease-Dokument abgebrochen werden. Lange oder mehrstufige Migrationen müssen daher kleine idempotente Batches verwenden und zwischen ihnen `context.checkpoint()` aufrufen. Die Statusschreibvorgänge sind durch Owner und Generation eingezäunt; bereits ausgeführte Geschäftsschreibvorgänge werden bei Lease-Verlust nicht automatisch zurückgerollt.

Die Lease-Zeitstempel werden derzeit vom Runner erzeugt. Alle Hosts, die Migrationen starten dürfen, müssen deshalb eine zuverlässig synchronisierte Systemzeit verwenden. Eine spätere Umstellung auf MongoDB-Serverzeit sollte erst nach einem Kompatibilitätstest mit der tatsächlich eingesetzten MongoDB-Version erfolgen.

## Fehler und Recovery

Vor `apply()` wird der Eintrag auf `running` gesetzt und der Versuchszähler erhöht. Bei Erfolg wird `completed`, bei einer normalen Exception `failed` mit Fehlerklasse, bereinigter Fehlermeldung und Laufzeit gespeichert.

Ein erneuter Lauf nimmt Migrationen mit `failed` oder einem nach Absturz verbliebenen `running` wieder auf. Deshalb muss jede Migration idempotent sein. Nach einem Prozessabsturz verhindert die ablaufende Lease eine dauerhafte Blockade.

Ein Absturz oder MongoDB-Fehler zwischen Geschäftsschreibvorgang und `completed`-Status kann einen teilweise ausgeführten Stand hinterlassen. Der Runner behauptet dann keinen erfolgreichen Abschluss. Beim nächsten Lauf wird dieselbe Migration erneut ausgeführt; deren idempotente Schreiblogik muss den Zwischenstand erkennen oder sicher wiederholen.

Unbekannte Datenbankversionen, ungültige Zustände, abweichende Namen und Checksum-Änderungen stoppen den Runner. Dadurch kann älterer Anwendungscode keine neuere Datenbank stillschweigend verändern.

## Sichere Nutzung in Staging und Production

1. Backup- und Restorefähigkeit vor strukturellen Migrationen nachweisen.
2. Zuerst einen Dry Run durchführen und Zielumgebung sowie Datenbank prüfen.
3. Migration in einer isolierten Testdatenbank testen.
4. Staging nur nach ausdrücklicher Freigabe migrieren.
5. Ergebnis in `schema_migrations` und den Anwendungslogs prüfen.
6. Production nur mit der doppelten, zielgebundenen Freigabe starten.
7. Bei Fehlern Ursache beheben und dieselbe idempotente Migration erneut ausführen oder eine neue korrigierende Version erstellen.

Es gibt kein automatisches destruktives Rollback. Datenrücksetzungen erfolgen nur über einen separat geprüften Restore- oder Korrekturplan.

## Noch mit echtem MongoDB zu verifizieren

Die automatisierten Tests verwenden eine isolierte In-Memory-Implementierung. Vor der ersten freigegebenen Staging-Migration müssen in einer separaten, entbehrlichen MongoDB-Testdatenbank zusätzlich geprüft werden:

- atomare Lease-Übernahme und `find_one_and_update(..., upsert=True)` unter echten Parallelzugriffen,
- Duplicate-Key-Verhalten des eindeutigen Versionsindex,
- Heartbeat-, Timeout- und Write-Concern-Verhalten bei Netzwerkunterbrechungen,
- BSON-Zeitwerte und Uhrensynchronisation zwischen beteiligten Hosts,
- Verfügbarkeit und Latenz des expliziten `majority` Read/Write Concern im Zielcluster,
- Recovery nach einem real abgebrochenen Prozess.
