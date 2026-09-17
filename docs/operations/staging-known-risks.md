# ORDO Staging – bekannte Risiken nach Arbeitspaket 0

Stand: 18. September 2026

Dieses Dokument erfasst ausschließlich Befunde der lesenden Bestandsaufnahme. Arbeitspaket 0 hat keine Daten, Anwendungskonfigurationen oder Deployments verändert.

## Offene Risiken

### 1. Backup und Restore sind nicht nachgewiesen

**Befund:** MongoDB Atlas ist bestätigt, die Backup-Konfiguration war mangels Atlas-Projektzugriff jedoch nicht einsehbar. Ein isolierter Restore wurde nicht ausgeführt.

**Auswirkung:** RPO und RTO können derzeit nicht belastbar zugesichert werden. Die tatsächliche Wiederherstellbarkeit ist unbekannt.

**Nächster Schritt:** Atlas-Zugriff bereitstellen, Backup-Einstellungen dokumentieren und einen Restore ausschließlich in ein neues isoliertes Testziel durchführen.

### 2. Staging-Deployment weicht von GitHub `main` ab

**Befund:** Der Render-Service `ordo-staging-api` lief zum Prüfzeitpunkt auf Commit `e3650ca515b3b5eec556a3a005d7a639f5f1ceb1`. Die verbindliche Entwicklungsbasis auf GitHub `main` ist `c4029819d6821f1c4bb12c68a8c34c6062ded53f`.

**Auswirkung:** Beobachtetes Laufzeitverhalten und aktueller Quellcode können voneinander abweichen. Spätere Prüfungen müssen klar angeben, ob sie den laufenden Service oder GitHub `main` betreffen.

**Nächster Schritt:** In einem ausdrücklich freigegebenen späteren Arbeitsschritt den Staging-Stand kontrolliert mit GitHub `main` abgleichen. Arbeitspaket 0 löst kein Deployment aus.

### 3. Uneindeutige Umgebungsbezeichnung

**Befund:** Der Service heißt `ordo-staging-api`, befindet sich im Render-Dashboard aber in einer Umgebung mit der Bezeichnung `Production`.

**Auswirkung:** Es besteht Verwechslungsgefahr bei Konfigurationsänderungen, Deployments und späteren Betriebsabläufen.

**Nächster Schritt:** Umgebungen und Benennung in einem späteren Infrastruktur-Arbeitspaket eindeutig trennen.

### 4. Automatisches Demo-Seeding beim Application-Startup

**Befund:** Der zum Prüfzeitpunkt untersuchte Startup-Code ruft das Seeding beim Start auf. In Render ist zwar eine Variable `ENABLE_DEMO_SEED` vorhanden, der untersuchte Startup-Code berücksichtigt sie nicht.

**Auswirkung:** Demo-Daten und die zugehörige Indexanlage sind weiterhin an den normalen Anwendungsstart gekoppelt. Das ist vor echten Produktionsdaten zu trennen.

**Nächster Schritt:** Ausschließlich im dafür vorgesehenen Folgepaket Seeding vom normalen Application-Startup trennen. Die vorhandenen 53 Dokumente dürfen dabei nicht ungeprüft verändert oder gelöscht werden.

### 5. Fachschlüssel teilweise nicht durch Unique Constraints geschützt

**Befund:** Für `companies.id`, `contracts.id`, `invoices.id`, `users.id` und `customer_prices.companyId + productId` bestehen keine eindeutigen Indizes. Aktuell wurden dennoch keine Duplikate gefunden.

**Auswirkung:** Zukünftige parallele oder fehlerhafte Schreibvorgänge könnten Duplikate erzeugen.

**Nächster Schritt:** Vor einer späteren Indexmigration Daten erneut prüfen, Sicherung und Restorefähigkeit nachweisen und erst danach versionierte, rückrollbare Indexänderungen planen.

### 6. Referenzen werden nicht von MongoDB erzwungen

**Befund:** Die geprüften Referenzen sind derzeit vollständig, werden aber durch Anwendungslogik statt durch Fremdschlüssel abgesichert.

**Auswirkung:** Fehlerhafte oder konkurrierende Schreibvorgänge können künftig verwaiste Referenzen erzeugen.

**Nächster Schritt:** Referenzprüfungen in den späteren Migrations- und Integritätspaketen beibehalten und für kritische Schreibvorgänge atomare Abläufe vorsehen.

### 7. Object-Storage-Bestand und Sicherung unbekannt

**Befund:** Es existiert keine Collection `uploads`. Ein externes Storage-Konto und dessen Sicherung waren nicht zugänglich.

**Auswirkung:** Über MongoDB referenzierte Dateien sind nicht vorhanden; außerhalb der Datenbank liegende, unreferenzierte Dateien können jedoch nicht ausgeschlossen werden. Wiederherstellbarkeit und Aufbewahrung sind unbekannt.

**Nächster Schritt:** Eigentümer und Anbieter des Speichers ermitteln und dessen Bestand, Versionierung und Backupfähigkeit lesend prüfen, bevor Dateien migriert oder gelöscht werden.

## Schutzvorgaben für folgende Arbeitspakete

- `ordo_staging` darf nicht überschrieben, geleert oder als Restore-Testziel verwendet werden.
- Vor Daten- oder Indexmigrationen müssen Backupstatus und Rückrollweg geklärt werden.
- Seed-/Demodaten dürfen nur nach erneuter Bestandsprüfung und ausdrücklicher Freigabe verändert werden.
- Secrets und Connection Strings dürfen nicht in Repository, Dokumentation oder Logs gelangen.
