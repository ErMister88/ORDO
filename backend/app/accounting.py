"""Provider-neutral, retryable accounting synchronization foundation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import secrets
from typing import Any, Mapping, Protocol

import httpx

from .background_jobs import BackgroundJobQueue
from .tenant_access import TenantBusinessAccess


class AccountingProviderError(RuntimeError):
    pass


def _environment_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AccountingContactResult:
    provider_contact_id: str


@dataclass(frozen=True)
class AccountingInvoiceResult:
    provider_invoice_id: str
    invoice_number: str | None = None
    document_reference: str | None = None


class AccountingProvider(Protocol):
    name: str

    async def sync_contact(self, *, external_key: str, company: Mapping[str, Any]) -> AccountingContactResult: ...

    async def sync_invoice(
        self,
        *,
        external_key: str,
        contact_id: str,
        document: Mapping[str, Any],
    ) -> AccountingInvoiceResult: ...


class SevdeskProvider:
    """Narrow sevdesk adapter. It is inert unless explicitly configured."""

    name = "sevdesk"

    def __init__(self, *, token: str | None = None, base_url: str | None = None) -> None:
        self._token = (token or os.getenv("SEVDESK_API_TOKEN") or "").strip()
        self._base_url = (base_url or os.getenv("SEVDESK_BASE_URL") or "https://my.sevdesk.de/api/v1").rstrip("/")
        if not self._token:
            raise AccountingProviderError("sevdesk_not_configured")
        environment = (os.getenv("APP_ENV") or "").strip().lower()
        if environment in {"prod", "production"} and not _environment_flag("SEVDESK_PRODUCTION_ENABLED"):
            raise AccountingProviderError("sevdesk_production_not_enabled")
        if environment in {"stage", "staging"} and not _environment_flag("SEVDESK_STAGING_WRITES_ENABLED"):
            raise AccountingProviderError("sevdesk_staging_writes_not_enabled")
        if environment not in {"dev", "development", "test", "testing", "stage", "staging", "prod", "production"}:
            raise AccountingProviderError("sevdesk_environment_not_configured")

    async def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self._base_url}/{path.lstrip('/')}",
                json=dict(payload),
                headers={"Authorization": self._token, "Accept": "application/json", "User-Agent": "ORDO accounting integration"},
            )
        if response.status_code >= 500 or response.status_code == 429:
            raise AccountingProviderError("sevdesk_retryable_failure")
        if response.status_code >= 400:
            raise AccountingProviderError("sevdesk_request_rejected")
        data = response.json()
        if not isinstance(data, Mapping):
            raise AccountingProviderError("sevdesk_response_invalid")
        return data

    async def _get(self, path: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self._base_url}/{path.lstrip('/')}", params=dict(params),
                headers={"Authorization": self._token, "Accept": "application/json", "User-Agent": "ORDO accounting integration"},
            )
        if response.status_code >= 500 or response.status_code == 429:
            raise AccountingProviderError("sevdesk_retryable_failure")
        if response.status_code >= 400:
            raise AccountingProviderError("sevdesk_request_rejected")
        data = response.json()
        if not isinstance(data, Mapping):
            raise AccountingProviderError("sevdesk_response_invalid")
        return data

    @staticmethod
    def _single_object(data: Mapping[str, Any], *, duplicate_error: str) -> Mapping[str, Any] | None:
        objects = data.get("objects")
        if objects in (None, []):
            return None
        if isinstance(objects, list):
            if len(objects) > 1:
                raise AccountingProviderError(duplicate_error)
            return objects[0] if objects and isinstance(objects[0], Mapping) else None
        return objects if isinstance(objects, Mapping) else None

    async def sync_contact(self, *, external_key: str, company: Mapping[str, Any]) -> AccountingContactResult:
        existing = self._single_object(
            await self._get("Contact", {"customerNumber": external_key, "depth": "1"}),
            duplicate_error="sevdesk_contact_duplicate_requires_review",
        )
        if existing:
            provider_id = existing.get("id")
            if isinstance(provider_id, str) and provider_id:
                return AccountingContactResult(provider_id)
        data = await self._post("Contact", {
            "name": company.get("name"),
            "customerNumber": external_key,
            "description": f"ORDO {external_key}",
        })
        objects = data.get("objects")
        row = objects[0] if isinstance(objects, list) and objects else objects
        provider_id = row.get("id") if isinstance(row, Mapping) else None
        if not isinstance(provider_id, str) or not provider_id:
            raise AccountingProviderError("sevdesk_contact_reference_missing")
        return AccountingContactResult(provider_id)

    async def sync_invoice(
        self, *, external_key: str, contact_id: str, document: Mapping[str, Any]
    ) -> AccountingInvoiceResult:
        existing = self._single_object(
            await self._get("Invoice", {"customerInternalNote": external_key, "showAll": "true"}),
            duplicate_error="sevdesk_invoice_duplicate_requires_review",
        )
        if existing:
            provider_id = existing.get("id")
            if isinstance(provider_id, str) and provider_id:
                return AccountingInvoiceResult(
                    provider_invoice_id=provider_id,
                    invoice_number=existing.get("invoiceNumber") if isinstance(existing.get("invoiceNumber"), str) else None,
                    document_reference=existing.get("documentReference") if isinstance(existing.get("documentReference"), str) else None,
                )
        # The provider draft is deliberately based only on ORDO's immutable
        # snapshot. Finalization/number authority remains an explicit setup step.
        data = await self._post("Invoice/Factory/saveInvoice", {
            "invoice": {
                "contact": {"id": contact_id, "objectName": "Contact"},
                "invoiceDate": document.get("date"),
                "header": f"ORDO {external_key}",
                "status": 100,
                "currency": document.get("currency"),
                "customerInternalNote": external_key,
            },
            "invoicePosSave": list(document.get("lineItems") or document.get("items") or []),
        })
        objects = data.get("objects")
        invoice = objects.get("invoice") if isinstance(objects, Mapping) else None
        provider_id = invoice.get("id") if isinstance(invoice, Mapping) else None
        if not isinstance(provider_id, str) or not provider_id:
            raise AccountingProviderError("sevdesk_invoice_reference_missing")
        return AccountingInvoiceResult(
            provider_invoice_id=provider_id,
            invoice_number=invoice.get("invoiceNumber") if isinstance(invoice.get("invoiceNumber"), str) else None,
            document_reference=invoice.get("documentReference") if isinstance(invoice.get("documentReference"), str) else None,
        )


def accounting_enabled() -> bool:
    if (os.getenv("ACCOUNTING_PROVIDER") or "").strip().lower() != "sevdesk":
        return False
    if not (os.getenv("SEVDESK_API_TOKEN") or "").strip():
        return False
    environment = (os.getenv("APP_ENV") or "").strip().lower()
    if environment in {"stage", "staging"}:
        return _environment_flag("SEVDESK_STAGING_WRITES_ENABLED")
    if environment in {"prod", "production"}:
        return _environment_flag("SEVDESK_PRODUCTION_ENABLED")
    return environment in {"dev", "development", "test", "testing"}


def configured_accounting_provider() -> AccountingProvider:
    provider = (os.getenv("ACCOUNTING_PROVIDER") or "").strip().lower()
    if provider != "sevdesk":
        raise AccountingProviderError("accounting_provider_not_configured")
    return SevdeskProvider()


async def schedule_accounting_sync(
    access: TenantBusinessAccess,
    *,
    resource_type: str,
    resource_id: str,
    actor_id: str,
) -> dict[str, Any]:
    if resource_type not in {"invoice", "shop_order"}:
        raise ValueError("Unsupported accounting resource")
    now = datetime.now(timezone.utc)
    sync_id = f"acct_{resource_type}_{resource_id}"
    initial_status = "pending" if accounting_enabled() else "not_configured"
    await access.accounting_syncs.update_one(
        {"resourceType": resource_type, "resourceId": resource_id},
        {
            "$setOnInsert": {
                "id": sync_id, "resourceType": resource_type, "resourceId": resource_id,
                "provider": "sevdesk", "status": initial_status,
                "createdAt": now, "updatedAt": now,
            },
        },
        upsert=True,
    )
    row = await access.accounting_syncs.find_one({"resourceType": resource_type, "resourceId": resource_id})
    if row and row.get("status") == "pending":
        await BackgroundJobQueue(access).enqueue(
            "accounting.sync", actor_id=actor_id,
            idempotency_key=f"accounting:{resource_type}:{resource_id}",
            payload={"syncId": row["id"]},
        )
    return row


async def request_accounting_retry(
    access: TenantBusinessAccess,
    *,
    sync_id: str,
    actor_id: str,
) -> dict[str, Any]:
    row = await access.accounting_syncs.find_one({"id": sync_id})
    if not row:
        raise AccountingProviderError("accounting_sync_missing")
    if not accounting_enabled():
        raise AccountingProviderError("accounting_provider_not_configured")
    if row.get("status") == "completed":
        return row
    resource_type, resource_id = row.get("resourceType"), row.get("resourceId")
    idempotency_key = f"accounting:{resource_type}:{resource_id}"
    queue = BackgroundJobQueue(access)
    job = await access.background_jobs.find_one({
        "jobType": "accounting.sync", "idempotencyKey": idempotency_key,
    })
    if job and job.get("status") in {"failed", "dead"}:
        await queue.retry(job["id"])
    elif not job:
        await queue.enqueue(
            "accounting.sync", actor_id=actor_id,
            idempotency_key=idempotency_key, payload={"syncId": sync_id},
        )
    elif job.get("status") not in {"pending", "processing"}:
        raise AccountingProviderError("accounting_retry_state_invalid")
    await access.accounting_syncs.update_one(
        {"id": sync_id, "status": {"$ne": "completed"}},
        {"$set": {"status": "pending", "updatedAt": datetime.now(timezone.utc)}},
    )
    return await access.accounting_syncs.find_one({"id": sync_id})


async def process_accounting_sync(
    access: TenantBusinessAccess,
    *,
    sync_id: str,
    provider: AccountingProvider | None = None,
) -> dict[str, Any]:
    row = await access.accounting_syncs.find_one({"id": sync_id})
    if not row:
        raise AccountingProviderError("accounting_sync_missing")
    resource_type = row.get("resourceType")
    resource_id = row.get("resourceId")
    collection = access.invoices if resource_type == "invoice" else access.shop_orders if resource_type == "shop_order" else None
    if collection is None:
        raise AccountingProviderError("accounting_resource_type_invalid")
    resource = await collection.find_one({"id": resource_id})
    if not resource:
        raise AccountingProviderError("accounting_resource_missing")
    if row.get("status") == "completed" and row.get("providerInvoiceId"):
        # Repair the narrow crash window after the sync record was completed
        # but before its provider references reached the business document.
        await collection.update_one({"id": resource_id}, {"$set": {
            "accountingProvider": row.get("provider"),
            "providerContactId": row.get("providerContactId"),
            "providerInvoiceId": row.get("providerInvoiceId"),
            "invoiceNumber": row.get("invoiceNumber"),
            "documentReference": row.get("documentReference"),
            "accountingSyncStatus": "completed",
            "accountingLastSyncAt": row.get("lastSyncAt"),
        }})
        return row
    company_id = resource.get("companyId")
    company = await access.companies.find_one({"id": company_id}) if isinstance(company_id, str) else None
    if company is None:
        customer = resource.get("customer") if isinstance(resource.get("customer"), Mapping) else {}
        company = {"id": f"shop:{resource_id}", "name": customer.get("name") or "B2C Kunde", "email": customer.get("email")}
    active_provider = provider or configured_accounting_provider()
    tenant_prefix = access.context.tenant_id
    contact_external_key = f"{tenant_prefix}:{company['id']}"
    invoice_external_key = f"{tenant_prefix}:{resource_type}:{resource_id}"
    contact_id = row.get("providerContactId") or company.get("providerContactId")
    if not isinstance(contact_id, str) or not contact_id:
        contact = await active_provider.sync_contact(external_key=contact_external_key, company=company)
        contact_id = contact.provider_contact_id
        await access.accounting_syncs.update_one({"id": sync_id}, {"$set": {
            "providerContactId": contact_id, "status": "contact_synced", "updatedAt": datetime.now(timezone.utc),
        }})
        if isinstance(company_id, str):
            await access.companies.update_one({"id": company_id}, {"$set": {
                "accountingProvider": active_provider.name,
                "providerContactId": contact_id,
                "accountingContactSyncedAt": datetime.now(timezone.utc),
            }})
    invoice_result = await active_provider.sync_invoice(
        external_key=invoice_external_key, contact_id=contact_id, document=resource,
    )
    now = datetime.now(timezone.utc)
    fields = {
        "status": "completed", "provider": active_provider.name,
        "providerInvoiceId": invoice_result.provider_invoice_id,
        "providerContactId": contact_id, "invoiceNumber": invoice_result.invoice_number,
        "documentReference": invoice_result.document_reference,
        "lastSyncAt": now, "updatedAt": now,
    }
    await access.accounting_syncs.update_one({"id": sync_id}, {"$set": fields})
    await collection.update_one({"id": resource_id}, {"$set": {
        "accountingProvider": active_provider.name,
        "providerContactId": contact_id,
        "providerInvoiceId": invoice_result.provider_invoice_id,
        "invoiceNumber": invoice_result.invoice_number,
        "documentReference": invoice_result.document_reference,
        "accountingSyncStatus": "completed", "accountingLastSyncAt": now,
    }})
    return await access.accounting_syncs.find_one({"id": sync_id})
