"""Add membership indexes and explicitly backfill legacy S&S internal users."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..models import Migration, MigrationPlan, MigrationStateError, checksum_file


MEMBERSHIP_COLLECTION = "tenant_memberships"
SS_TENANT_ID = "tnt_ss_0001"
INTERNAL_ROLES = {"admin", "sales", "customer"}


@dataclass(frozen=True)
class IndexSpec:
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool


INDEX_SPECS = (
    IndexSpec("uniq_tenant_membership_id", (("id", ASCENDING),), True),
    IndexSpec(
        "uniq_tenant_membership_user",
        (("tenantId", ASCENDING), ("userId", ASCENDING)),
        True,
    ),
    IndexSpec(
        "idx_tenant_membership_lookup",
        (("tenantId", ASCENDING), ("status", ASCENDING), ("role", ASCENDING)),
        False,
    ),
    IndexSpec(
        "idx_user_membership_lookup",
        (("userId", ASCENDING), ("status", ASCENDING), ("tenantId", ASCENDING)),
        False,
    ),
)


def membership_id(tenant_id: str, user_id: str) -> str:
    digest = sha256(f"{tenant_id}:{user_id}".encode("utf-8")).hexdigest()[:24]
    return f"mbr_{digest}"


def _normalized_keys(index: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    return tuple((str(field), int(direction)) for field, direction in index.get("key", []))


def _index_matches(index: Mapping[str, Any], spec: IndexSpec) -> bool:
    return (
        _normalized_keys(index) == spec.keys
        and bool(index.get("unique", False)) is spec.unique
    )


def _index_state(database, spec: IndexSpec) -> str:
    information = database[MEMBERSHIP_COLLECTION].index_information()
    named = information.get(spec.name)
    if named is not None:
        if not _index_matches(named, spec):
            raise MigrationStateError(
                f"Index {MEMBERSHIP_COLLECTION}.{spec.name} has another definition"
            )
        return "unchanged"
    for existing_name, existing in information.items():
        if existing_name != "_id_" and _normalized_keys(existing) == spec.keys:
            raise MigrationStateError(
                f"Membership index keys for {spec.name} already exist as {existing_name}"
            )
    return "create"


def _expected_membership(user: Mapping[str, Any]) -> dict[str, Any] | None:
    role = user.get("role")
    if role not in INTERNAL_ROLES:
        return None
    user_id = user.get("id")
    if not isinstance(user_id, str) or not user_id or user_id != user_id.strip():
        raise MigrationStateError("Legacy internal user has an invalid id")
    company_id = user.get("companyId") if role == "customer" else None
    if role == "customer":
        if not isinstance(company_id, str) or not company_id:
            raise MigrationStateError(
                f"Legacy customer {user_id!r} has no valid companyId"
            )
    return {
        "id": membership_id(SS_TENANT_ID, user_id),
        "tenantId": SS_TENANT_ID,
        "userId": user_id,
        "role": role,
        "status": "active",
        "companyId": company_id,
    }


def _validate_tenant(database, *, required: bool) -> None:
    tenants = list(database["tenants"].find({"id": SS_TENANT_ID}))
    if not tenants and not required:
        return
    if len(tenants) != 1 or tenants[0].get("status") != "active":
        raise MigrationStateError("S&S tenant must exist uniquely and be active")


def _validate_existing(document: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    for field, value in expected.items():
        if document.get(field) != value:
            raise MigrationStateError(
                f"Membership {expected['id']} conflicts in field {field}"
            )
    if not isinstance(document.get("createdAt"), str) or not isinstance(
        document.get("updatedAt"), str
    ):
        raise MigrationStateError(
            f"Membership {expected['id']} has invalid timestamps"
        )


def _validate_membership_invariants(database, document: Mapping[str, Any]) -> None:
    role = document.get("role")
    status = document.get("status")
    company_id = document.get("companyId")
    if role not in INTERNAL_ROLES:
        raise MigrationStateError("Existing membership has an invalid role")
    if status not in {"active", "inactive"}:
        raise MigrationStateError("Existing membership has an invalid status")
    if not isinstance(document.get("createdAt"), str) or not isinstance(
        document.get("updatedAt"), str
    ):
        raise MigrationStateError("Existing membership has invalid timestamps")
    if role == "customer":
        if not isinstance(company_id, str) or not company_id:
            raise MigrationStateError(
                "Existing customer membership requires a company"
            )
        if not database["companies"].find_one({
            "tenantId": document["tenantId"],
            "id": company_id,
        }):
            raise MigrationStateError(
                "Existing customer membership references no tenant company"
            )
    elif company_id is not None:
        raise MigrationStateError(
            "Existing non-customer membership cannot reference a company"
        )


def _analyze(database) -> dict[str, Any]:
    users = list(database["users"].find({}))
    expected = [item for user in users if (item := _expected_membership(user))]
    existing_memberships = list(database[MEMBERSHIP_COLLECTION].find({}))
    _validate_tenant(database, required=bool(expected or existing_memberships))
    seen_users: set[str] = set()
    seen_ids: set[str] = set()
    for document in existing_memberships:
        user_id = document.get("userId")
        membership_identifier = document.get("id")
        tenant_id = document.get("tenantId")
        if not all(
            isinstance(value, str) and value and value == value.strip()
            for value in (user_id, membership_identifier, tenant_id)
        ):
            raise MigrationStateError("Existing membership has invalid identity fields")
        tenant_user = f"{tenant_id}\0{user_id}"
        if tenant_user in seen_users or membership_identifier in seen_ids:
            raise MigrationStateError("Existing memberships contain duplicate identities")
        seen_users.add(tenant_user)
        seen_ids.add(membership_identifier)
        if not database["users"].find_one({"id": user_id}):
            raise MigrationStateError("Existing membership references an unknown user")
        tenant = database["tenants"].find_one({"id": tenant_id})
        if not tenant:
            raise MigrationStateError("Existing membership references an unknown tenant")
        _validate_membership_invariants(database, document)

    pending = 0
    unchanged = 0
    for document in expected:
        if document["role"] == "customer" and not database["companies"].find_one({
            "tenantId": SS_TENANT_ID,
            "id": document["companyId"],
        }):
            raise MigrationStateError(
                f"Customer membership {document['id']} references no S&S company"
            )
        matches = list(database[MEMBERSHIP_COLLECTION].find({
            "$or": [
                {"id": document["id"]},
                {"tenantId": SS_TENANT_ID, "userId": document["userId"]},
            ]
        }))
        if not matches:
            pending += 1
        elif len(matches) == 1:
            _validate_existing(matches[0], document)
            unchanged += 1
        else:
            raise MigrationStateError("Multiple memberships conflict with a backfill identity")

    actions = [
        {
            "name": spec.name,
            "keys": list(spec.keys),
            "unique": spec.unique,
            "action": _index_state(database, spec),
        }
        for spec in INDEX_SPECS
    ]
    return {
        "legacyInternalUsers": len(expected),
        "membershipsToInsert": pending,
        "membershipsAlreadyCorrect": unchanged,
        "shopUsersExcluded": sum(user.get("role") == "shopuser" for user in users),
        "otherGlobalIdentitiesExcluded": sum(
            user.get("role") not in INTERNAL_ROLES | {"shopuser"}
            for user in users
        ),
        "globalUsersModified": 0,
        "indexActions": actions,
        "indexesToCreate": sum(action["action"] == "create" for action in actions),
    }


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "The canonical active S&S tenant exists",
            "Every legacy internal user has a supported role",
            "Every legacy customer references an S&S tenant-scoped company",
            "Existing memberships reference known users and tenants without duplicates",
            "Shop users remain global B2C identities without tenant membership",
        ),
        expected_changes=_analyze(database),
    )


def _create_index(database, spec: IndexSpec) -> None:
    database[MEMBERSHIP_COLLECTION].create_index(
        list(spec.keys),
        name=spec.name,
        unique=spec.unique,
    )
    if _index_state(database, spec) != "unchanged":
        raise MigrationStateError(f"Membership index {spec.name} was not persisted")


def apply(database, context) -> dict[str, Any]:
    analysis = _analyze(database)
    indexes_created = 0
    for spec, action in zip(INDEX_SPECS, analysis["indexActions"]):
        context.checkpoint()
        if action["action"] == "create":
            _create_index(database, spec)
            indexes_created += 1
        context.checkpoint()

    inserted = 0
    for user in database["users"].find({}):
        expected = _expected_membership(user)
        if expected is None:
            continue
        matches = list(database[MEMBERSHIP_COLLECTION].find({
            "$or": [
                {"id": expected["id"]},
                {"tenantId": SS_TENANT_ID, "userId": expected["userId"]},
            ]
        }))
        if matches:
            if len(matches) != 1:
                raise MigrationStateError("Multiple memberships conflict during backfill")
            _validate_existing(matches[0], expected)
            continue
        now = datetime.now(timezone.utc).isoformat()
        document = {**expected, "createdAt": now, "updatedAt": now}
        context.checkpoint()
        try:
            database[MEMBERSHIP_COLLECTION].insert_one(document)
            inserted += 1
        except DuplicateKeyError:
            current = database[MEMBERSHIP_COLLECTION].find_one({
                "tenantId": SS_TENANT_ID,
                "userId": expected["userId"],
            })
            if current is None:
                raise
            _validate_existing(current, expected)
        context.checkpoint()

    return {
        "membershipsInserted": inserted,
        "membershipsAlreadyCorrect": analysis["legacyInternalUsers"] - inserted,
        "globalUsersModified": 0,
        "indexesCreated": indexes_created,
    }


MIGRATION = Migration(
    version=3,
    name="tenant_memberships",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
