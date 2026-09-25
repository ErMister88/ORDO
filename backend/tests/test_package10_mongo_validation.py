from __future__ import annotations

import pytest

from scripts.validate_package10_mongo import validated_target


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
