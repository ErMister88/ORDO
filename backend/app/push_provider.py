"""Provider-neutral push transport; Expo is the current implementation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol

import httpx


class PushProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class PushMessage:
    device_tokens: tuple[str, ...]
    title: str
    body: str
    data: dict


class PushProvider(Protocol):
    name: str
    async def send(self, message: PushMessage) -> int: ...


class ExpoPushProvider:
    name = "expo"
    endpoint = "https://exp.host/--/api/v2/push/send"

    async def send(self, message: PushMessage) -> int:
        headers = {"Content-Type": "application/json"}
        access_token = (os.getenv("EXPO_ACCESS_TOKEN") or "").strip()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        payload = [
            {"to": token, "title": message.title, "body": message.body, "data": message.data}
            for token in message.device_tokens
        ]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PushProviderError("Push provider unavailable") from exc
        try:
            result = response.json()
        except ValueError as exc:
            raise PushProviderError("Push provider returned an invalid response") from exc
        tickets = result.get("data") if isinstance(result, dict) else None
        if not isinstance(tickets, list) or any(ticket.get("status") == "error" for ticket in tickets):
            raise PushProviderError("Push provider rejected one or more messages")
        return len(tickets)


def get_push_provider() -> PushProvider:
    backend = (os.getenv("PUSH_BACKEND") or "").strip().lower()
    if backend != "expo":
        raise PushProviderError("Push provider is not configured")
    return ExpoPushProvider()
