from __future__ import annotations

import asyncio
import inspect

import pytest

from app.tenancy import (
    SS_TENANT_ID,
    SingleTenantResolver,
    Tenant,
    TenantContext,
    TenantResolutionError,
    TenantResolutionSource,
    TenantResolutionSubject,
    TenantStatus,
    TenancyConfigurationError,
    TenancyMode,
    TenancySettings,
)


class StubTenantDirectory:
    def __init__(self, tenants: list[Tenant]) -> None:
        self.tenants = tenants
        self.reads = 0

    async def list_tenants(self):
        self.reads += 1
        return tuple(self.tenants)


def tenant(
    tenant_id: str = SS_TENANT_ID,
    *,
    status: TenantStatus = TenantStatus.ACTIVE,
) -> Tenant:
    return Tenant(
        tenant_id=tenant_id,
        slug="ss-coffee-and-more" if tenant_id == SS_TENANT_ID else "other",
        display_name="S&S coffee and more" if tenant_id == SS_TENANT_ID else "Other",
        legal_name=None,
        status=status,
    )


def settings(values: dict[str, str] | None = None) -> TenancySettings:
    return TenancySettings.from_mapping(values or {
        "TENANCY_MODE": "single",
        "DEFAULT_TENANT_ID": SS_TENANT_ID,
    })


def resolve(resolver: SingleTenantResolver, subject: TenantResolutionSubject | None = None):
    return asyncio.run(resolver.resolve(subject))


def test_valid_single_tenant_configuration_and_resolution():
    directory = StubTenantDirectory([tenant()])
    resolver = SingleTenantResolver(settings(), directory)

    context = resolve(resolver, TenantResolutionSubject(actor_user_id="u-admin"))

    assert context == TenantContext(
        tenant_id=SS_TENANT_ID,
        actor_user_id="u-admin",
        membership_id=None,
        role=None,
        resolution_source=TenantResolutionSource.SINGLE_TENANT_CONFIGURATION,
    )
    assert directory.reads == 1


@pytest.mark.parametrize("values", [
    {},
    {"DEFAULT_TENANT_ID": SS_TENANT_ID},
    {"TENANCY_MODE": "   ", "DEFAULT_TENANT_ID": SS_TENANT_ID},
])
def test_missing_tenancy_mode_is_rejected(values):
    with pytest.raises(TenancyConfigurationError, match="TENANCY_MODE is required"):
        TenancySettings.from_mapping(values)


def test_environment_loader_has_no_implicit_defaults(monkeypatch):
    monkeypatch.delenv("TENANCY_MODE", raising=False)
    monkeypatch.delenv("DEFAULT_TENANT_ID", raising=False)

    with pytest.raises(TenancyConfigurationError, match="TENANCY_MODE is required"):
        TenancySettings.from_environment()


@pytest.mark.parametrize("mode", ["multi", "disabled", "production", "1"])
def test_unsupported_tenancy_mode_is_rejected(mode):
    with pytest.raises(TenancyConfigurationError, match="Unsupported TENANCY_MODE"):
        settings({"TENANCY_MODE": mode, "DEFAULT_TENANT_ID": SS_TENANT_ID})


@pytest.mark.parametrize("values", [
    {"TENANCY_MODE": "single"},
    {"TENANCY_MODE": "single", "DEFAULT_TENANT_ID": "   "},
])
def test_missing_default_tenant_id_is_rejected(values):
    with pytest.raises(TenancyConfigurationError, match="DEFAULT_TENANT_ID is required"):
        TenancySettings.from_mapping(values)


@pytest.mark.parametrize("tenant_id", ["tnt_other_0001", "TNT_SS_0001", "tnt_ss_0002"])
def test_wrong_default_tenant_id_is_rejected(tenant_id):
    with pytest.raises(TenancyConfigurationError, match="immutable S&S tenant id"):
        settings({"TENANCY_MODE": "single", "DEFAULT_TENANT_ID": tenant_id})


def test_mode_is_case_and_whitespace_tolerant_but_tenant_id_is_case_sensitive():
    parsed = settings({
        "TENANCY_MODE": "  SiNgLe  ",
        "DEFAULT_TENANT_ID": f"  {SS_TENANT_ID}  ",
    })

    assert parsed.mode is TenancyMode.SINGLE
    assert parsed.default_tenant_id == SS_TENANT_ID

    with pytest.raises(TenancyConfigurationError):
        settings({"TENANCY_MODE": "single", "DEFAULT_TENANT_ID": "TNT_SS_0001"})


def test_unknown_configured_tenant_is_rejected_without_fallback():
    directory = StubTenantDirectory([tenant("tnt_other_0001")])

    with pytest.raises(TenantResolutionError, match="does not exist uniquely"):
        resolve(SingleTenantResolver(settings(), directory))


def test_inactive_configured_tenant_is_rejected():
    directory = StubTenantDirectory([tenant(status=TenantStatus.INACTIVE)])

    with pytest.raises(TenantResolutionError, match="is not active"):
        resolve(SingleTenantResolver(settings(), directory))


def test_multiple_active_tenants_are_rejected_in_single_mode():
    directory = StubTenantDirectory([tenant(), tenant("tnt_other_0001")])

    with pytest.raises(TenantResolutionError, match="exactly one active"):
        resolve(SingleTenantResolver(settings(), directory))


def test_additional_inactive_tenant_does_not_create_an_ambiguous_active_context():
    directory = StubTenantDirectory([
        tenant(),
        tenant("tnt_other_0001", status=TenantStatus.INACTIVE),
    ])

    context = resolve(SingleTenantResolver(settings(), directory))

    assert context.tenant_id == SS_TENANT_ID


def test_duplicate_configured_tenant_records_are_rejected():
    directory = StubTenantDirectory([tenant(), tenant()])

    with pytest.raises(TenantResolutionError, match="does not exist uniquely"):
        resolve(SingleTenantResolver(settings(), directory))


def test_empty_tenant_directory_has_no_silent_fallback():
    with pytest.raises(TenantResolutionError, match="does not exist uniquely"):
        resolve(SingleTenantResolver(settings(), StubTenantDirectory([])))


def test_client_cannot_override_tenant_context_through_resolver_api():
    signature = inspect.signature(SingleTenantResolver.resolve)

    assert "tenant_id" not in signature.parameters
    assert "tenantId" not in signature.parameters
    with pytest.raises(TypeError):
        asyncio.run(
            SingleTenantResolver(settings(), StubTenantDirectory([tenant()])).resolve(
                **{"tenant_id": "tnt_other_0001"}
            )
        )


def test_resolution_contract_is_not_coupled_to_legacy_user_role_or_company():
    subject_fields = set(TenantResolutionSubject.__dataclass_fields__)

    assert subject_fields == {"actor_user_id"}
    assert "companyId" not in subject_fields
    assert "role" not in subject_fields


def test_tenancy_module_has_no_database_or_business_document_side_effects():
    directory = StubTenantDirectory([tenant()])

    context = resolve(SingleTenantResolver(settings(), directory))

    assert context.tenant_id == SS_TENANT_ID
    assert directory.tenants == [tenant()]
    assert not hasattr(directory, "insert_one")
    assert not hasattr(directory, "update_one")
