"""MongoDB-backed tenant directory used by the server-side resolver."""

from __future__ import annotations

from .domain import Tenant, TenantStatus
from .resolver import TenantResolutionError


class MongoTenantDirectory:
    def __init__(self, database) -> None:
        self._database = database

    async def list_tenants(self):
        # Single-tenant mode must inspect the complete directory. A fixed limit
        # could otherwise hide an additional active tenant and defeat the
        # resolver's "exactly one active tenant" invariant.
        documents = await self._database.tenants.find({}).to_list(length=None)
        try:
            return tuple(
                Tenant(
                    tenant_id=document["id"],
                    slug=document["slug"],
                    display_name=document["displayName"],
                    legal_name=document.get("legalName"),
                    status=TenantStatus(document["status"]),
                    default_currency=document["defaultCurrency"],
                    default_locale=document["defaultLocale"],
                    timezone=document["timezone"],
                )
                for document in documents
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TenantResolutionError("Tenant directory contains an invalid document") from exc
