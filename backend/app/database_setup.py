"""Idempotent database infrastructure required by the running application."""

from __future__ import annotations


async def ensure_required_indexes(database) -> None:
    """Preserve the indexes that existed before demo seeding was separated."""

    await database.users.create_index("email", unique=True, name="uniq_email")
    await database.password_resets.create_index(
        "expiresAt",
        expireAfterSeconds=0,
        name="ttl_reset",
    )
    await database.auth_rate_limits.create_index(
        "expiresAt",
        expireAfterSeconds=0,
        name="ttl_auth_rate_limit",
    )
    await database.offers.create_index("id", unique=True, name="uniq_offer_id")
    await database.orders.create_index("id", unique=True, name="uniq_order_id")
    await database.products.create_index("id", unique=True, name="uniq_product_id")
