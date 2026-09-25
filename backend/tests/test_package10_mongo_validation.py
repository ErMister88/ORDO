from __future__ import annotations

import pytest

from scripts.validate_package10_mongo import (
    EXPECTED_MIGRATION_SETTINGS,
    ensure_no_business_documents,
    validated_target,
)


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, *, length):
        return self.documents[:length]


class Collection:
    def __init__(self, documents):
        self.documents = documents

    async def count_documents(self, _query):
        return len(self.documents)

    def find(self, _query, _projection):
        return Cursor([{key: value for key, value in document.items() if key != "_id"}
                       for document in self.documents])


class Database:
    def __init__(self, collections):
        self.collections = {name: Collection(documents) for name, documents in collections.items()}

    async def list_collection_names(self):
        return list(self.collections)

    def __getitem__(self, name):
        return self.collections[name]


def target(**overrides: str) -> dict[str, str]:
    values = {
        "APP_ENV": "test",
        "DB_NAME": "ordo_test_package10_validation",
        "MONGO_URL": "mongodb://example.invalid",
        "ORDO_TEST_TARGET_CONFIRMATION": "test:ordo_test_package10_validation",
    }
    values.update(overrides)
    return values


def test_validation_target_requires_exact_disposable_database_confirmation():
    result = validated_target(target())
    assert result.database_name == "ordo_test_package10_validation"
    assert result.confirmation == "test:ordo_test_package10_validation"


@pytest.mark.parametrize(
    "overrides",
    [
        {"APP_ENV": "staging", "DB_NAME": "ordo_staging", "ORDO_TEST_TARGET_CONFIRMATION": "staging:ordo_staging"},
        {"APP_ENV": "production", "DB_NAME": "ordo_test_package10_validation", "ORDO_TEST_TARGET_CONFIRMATION": "production:ordo_test_package10_validation"},
        {"DB_NAME": "ordo_staging", "ORDO_TEST_TARGET_CONFIRMATION": "test:ordo_staging"},
        {"DB_NAME": "package10", "ORDO_TEST_TARGET_CONFIRMATION": "test:package10"},
        {"ORDO_TEST_TARGET_CONFIRMATION": "test:another_database"},
        {"MONGO_URL": ""},
    ],
)
def test_validation_target_fails_closed(overrides):
    with pytest.raises(RuntimeError):
        validated_target(target(**overrides))


def test_migration_created_shop_settings_are_allowed():
    database = Database({"settings": [{"_id": "mongo-id", **EXPECTED_MIGRATION_SETTINGS}]})

    import asyncio
    asyncio.run(ensure_no_business_documents(database))


@pytest.mark.parametrize(
    "collections",
    [
        {"settings": [{**EXPECTED_MIGRATION_SETTINGS, "shippingFeeMinor": 490}]},
        {"settings": [EXPECTED_MIGRATION_SETTINGS, EXPECTED_MIGRATION_SETTINGS]},
        {"settings": [{"tenantId": "tnt_other", "key": "shop"}]},
        {"orders": [{"tenantId": "tnt_ss_0001", "id": "ord-existing"}]},
    ],
)
def test_existing_business_or_unexpected_settings_fail_closed(collections):
    database = Database(collections)

    import asyncio
    with pytest.raises(RuntimeError):
        asyncio.run(ensure_no_business_documents(database))
