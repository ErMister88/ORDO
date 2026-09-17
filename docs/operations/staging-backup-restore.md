# ORDO Staging – Backup und Restore

Stand der Bestandsaufnahme: 18. September 2026

Betroffene Datenbank: `ordo_staging` auf MongoDB Atlas

## Nachgewiesener Status

MongoDB Atlas wurde als Datenbankanbieter bestätigt. Die Atlas-Verwaltungsoberfläche war im verfügbaren Browser jedoch abgemeldet und es lagen keine ausreichenden Projektberechtigungen zur Einsicht der Backup-Konfiguration vor.

Folgende Punkte konnten deshalb nicht verifiziert werden:

- Backup-Art und Atlas-Cluster-Tier
- Backup-Frequenz
- Aufbewahrungsdauer
- Zeitpunkt und Status der letzten erfolgreichen Sicherung
- Verfügbarkeit von Point-in-Time-Restore
- Berechtigungen für einen Restore
- Backup oder Versionierung eines möglicherweise vorhandenen Object Storage

Es wurde **kein Backup als erfolgreich bestätigt** und **kein Restore durchgeführt**.

## Derzeit belastbar zusicherbare RPO- und RTO-Werte

| Kennzahl | Status |
| --- | --- |
| RPO – maximal tolerierbarer Datenverlust | Nicht bestimmbar, solange kein Backup und keine Aufbewahrung nachgewiesen sind |
| RTO – erwartete Wiederherstellungsdauer | Nicht bestimmbar, solange kein isolierter Restore erfolgreich gemessen wurde |

Bis zum erfolgreichen Nachweis muss vollständiger Verlust der seit der letzten tatsächlich verfügbaren Sicherung entstandenen Daten als Worst Case berücksichtigt werden.

## Benötigte Zugriffe und Informationen

Für den Abschluss der Prüfung werden benötigt:

1. Zugriff auf das konkrete MongoDB-Atlas-Projekt mit Leserechten für Cluster- und Backup-Konfiguration.
2. Sicht auf vorhandene Snapshots, Aufbewahrungsregeln und Point-in-Time-Einstellungen.
3. Berechtigung, einen Restore ausschließlich in ein neues isoliertes Testziel durchzuführen.
4. Falls Object Storage verwendet wird: Name des Anbieters sowie lesender Zugriff auf Versionierungs-, Lifecycle- und Backup-Einstellungen.

Connection Strings, Passwörter und Tokens dürfen weder in diesem Dokument noch in Git gespeichert werden.

## Vorgesehenes Restore-Verfahren

Dieses Verfahren ist ein Runbook für einen späteren Test. Es wurde in Arbeitspaket 0 nicht ausgeführt.

1. Vor Beginn den aktuellen Staging-Datenbanknamen `ordo_staging` dokumentieren und ausdrücklich als unzulässiges Restore-Ziel markieren.
2. Geeigneten Snapshot beziehungsweise, falls verfügbar, einen Point-in-Time-Zeitpunkt auswählen.
3. Ein neues, eindeutig benanntes und isoliertes Testziel anlegen. Die bestehende Staging-Datenbank darf niemals überschrieben, geleert oder als Ziel gewählt werden.
4. Wiederherstellung ausschließlich in dieses Testziel starten.
5. Anwendung und Render-Service nicht auf das Testziel umstellen.
6. Nach Abschluss mindestens folgende Punkte vergleichen:
   - vorhandene Collections,
   - Dokumentzahlen je Collection,
   - Indexnamen und Indexoptionen,
   - eindeutige und TTL-Indizes,
   - fehlende oder doppelte Fach-IDs,
   - verwaiste Referenzen.
7. Beginn, Ende, Ergebnis, Fehler und gemessene Wiederherstellungsdauer dokumentieren.
8. Das Testziel erst nach einer gesonderten Freigabe und außerhalb dieses Arbeitspakets entfernen.

## Abnahmekriterien für einen späteren Restore-Test

Ein Restore gilt erst dann als erfolgreich getestet, wenn:

- das Ziel nachweislich von Staging isoliert war,
- die Wiederherstellung ohne Schreibzugriff auf `ordo_staging` abgeschlossen wurde,
- Collections, Dokumentzahlen und Indizes mit dem gewählten Sicherungszeitpunkt übereinstimmen,
- die geprüften Referenzen konsistent sind,
- RPO und RTO aus realen Zeitpunkten berechnet und dokumentiert wurden.

Bis diese Kriterien erfüllt sind, bleibt der Backup- und Restore-Nachweis offen.
