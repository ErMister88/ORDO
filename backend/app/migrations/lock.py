"""MongoDB-backed migration lease with automatic renewal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .models import MigrationLockLost, MigrationLockUnavailable, safe_error_message


LOCK_COLLECTION = "schema_migration_lock"
LOCK_ID = "global"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MigrationLease:
    """A renewable, owner-checked lease stored under MongoDB's unique `_id`."""

    def __init__(
        self,
        database,
        *,
        application_version: str,
        lease_seconds: float = 60,
        owner_id: str | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.database = database
        self.collection = database[LOCK_COLLECTION].with_options(
            read_concern=ReadConcern("majority"),
            write_concern=WriteConcern("majority"),
        )
        self.application_version = application_version
        self.lease_seconds = float(lease_seconds)
        self.owner_id = owner_id or uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lost_reason: str | None = None
        self.generation: int | None = None

    def acquire(self, *, start_heartbeat: bool = True) -> None:
        now = utc_now()
        expires_at = now + timedelta(seconds=self.lease_seconds)
        try:
            document = self.collection.find_one_and_update(
                {
                    "_id": LOCK_ID,
                    "$or": [
                        {"expiresAt": {"$lte": now}},
                        {"expiresAt": {"$exists": False}},
                        {"ownerId": self.owner_id},
                    ],
                },
                {
                    "$set": {
                        "ownerId": self.owner_id,
                        "acquiredAt": now,
                        "heartbeatAt": now,
                        "expiresAt": expires_at,
                        "applicationVersion": self.application_version,
                    },
                    "$inc": {"generation": 1},
                    "$unset": {"releasedAt": ""},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise MigrationLockUnavailable("Another migration runner holds the lease") from exc

        if not document or document.get("ownerId") != self.owner_id:
            raise MigrationLockUnavailable("Another migration runner holds the lease")
        self.generation = int(document["generation"])

        if start_heartbeat:
            self._thread = threading.Thread(
                target=self._heartbeat_loop,
                name="ordo-migration-lease",
                daemon=True,
            )
            self._thread.start()

    def renew(self) -> None:
        if self._lost_reason:
            raise MigrationLockLost(self._lost_reason)
        now = utc_now()
        result = self.collection.update_one(
            {
                "_id": LOCK_ID,
                "ownerId": self.owner_id,
                "generation": self.generation,
                "expiresAt": {"$gt": now},
            },
            {
                "$set": {
                    "heartbeatAt": now,
                    "expiresAt": now + timedelta(seconds=self.lease_seconds),
                }
            },
        )
        if result.matched_count != 1:
            self._lost_reason = "Lease ownership changed"
            raise MigrationLockLost(self._lost_reason)

    def assert_owned(self) -> None:
        if self._lost_reason:
            raise MigrationLockLost(self._lost_reason)
        document = self.collection.find_one(
            {
                "_id": LOCK_ID,
                "ownerId": self.owner_id,
                "generation": self.generation,
            }
        )
        if not document:
            raise MigrationLockLost("Lease ownership changed")
        expires_at = document.get("expiresAt")
        if not expires_at:
            raise MigrationLockLost("Lease has no expiry")
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utc_now():
            raise MigrationLockLost("Lease expired")

    def release(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self.lease_seconds))
        now = utc_now()
        self.collection.update_one(
            {
                "_id": LOCK_ID,
                "ownerId": self.owner_id,
                "generation": self.generation,
            },
            {
                "$set": {"expiresAt": now, "releasedAt": now},
                "$unset": {"ownerId": ""},
            },
        )

    def _heartbeat_loop(self) -> None:
        interval = max(0.05, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                self.renew()
            except Exception as exc:  # The foreground runner checks this before committing status.
                self._lost_reason = safe_error_message(exc)
                self._stop.set()
                return

    def __enter__(self) -> "MigrationLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
