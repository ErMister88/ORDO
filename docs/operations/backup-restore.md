# MongoDB backup and isolated restore

## Safety model

Backups use MongoDB Database Tools (`mongodump` and `mongorestore`). The MongoDB URI is passed through a temporary mode-0600 config file and is never printed or stored in a manifest. A backup is complete only after the compressed archive exists, has a non-zero size and an atomic manifest with its SHA-256 checksum has been written.

The manifest records environment, database identifier, migration version, collection counts, index names, archive size and checksum. It contains no documents or secrets.

## Create a backup

```bash
cd backend
python -m scripts.backup_database --output /secure/backup/location
```

`APP_ENV`, `DB_NAME` and `MONGO_URL` must be provided as secrets by the runtime. Production backup execution additionally requires the explicit target-bound argument `--production-approval backup:<database>`. Package 11 does not execute a production backup.

## Restore to an isolated database

Restore refuses production environments, the source database and any non-empty target. The target name must begin with `ordo_restore_`.

```bash
cd backend
python -m scripts.restore_database \
  --manifest /secure/backup/location/<archive>.archive.gz.json \
  --target-db ordo_restore_<unique-name>
```

After restore, ORDO verifies database connectivity, expected collections and counts, index names, migration ledger version and checksums, and tenant membership references. A verification report is written beside the manifest. A failed verification is never reported as success and the isolated database is retained for diagnosis. The source database is never modified or used as a restore target.

## Operational minimum

- Encrypt the backup destination and restrict access to operations staff.
- Keep database and object-storage backups in independent failure domains.
- Monitor failed or missing backup runs.
- Perform a scheduled isolated restore verification and record the achieved RPO/RTO.
- Do not define deletion or retention periods until the owner has approved the legal and business requirements.
