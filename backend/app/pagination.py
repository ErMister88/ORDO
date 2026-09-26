"""Small, response-compatible bounds for list endpoints."""

from __future__ import annotations

from fastapi import HTTPException


DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500


def validate_page(limit: int, offset: int, *, maximum: int = MAX_PAGE_SIZE) -> tuple[int, int]:
    """Validate direct calls as strictly as FastAPI validates HTTP query parameters."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > maximum:
        raise HTTPException(status_code=422, detail=f"limit muss zwischen 1 und {maximum} liegen")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 100_000:
        raise HTTPException(status_code=422, detail="offset muss zwischen 0 und 100000 liegen")
    return limit, offset


async def bounded_list(cursor, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0, maximum: int = MAX_PAGE_SIZE):
    """Read one bounded page without changing the existing array response shape.

    Motor supports ``skip``. The small fallback keeps isolated in-memory test
    adapters honest without making production code depend on them.
    """

    limit, offset = validate_page(limit, offset, maximum=maximum)
    if offset and hasattr(cursor, "skip"):
        cursor = cursor.skip(offset)
        return await cursor.to_list(limit)
    rows = await cursor.to_list(limit + offset)
    return rows[offset:offset + limit]
