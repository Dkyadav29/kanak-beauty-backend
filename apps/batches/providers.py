"""
Live-class conferencing provider adapters.

The student join endpoint never hands out ``LiveSession.provider_meeting_ref``
or any static meeting credential. Instead it asks the configured adapter for
a short-lived, backend-signed join URL that is bound to the session and the
user. The adapter is selected by ``settings.LIVE_PROVIDER``:

- ``internal`` (default): the academy's own browser room. The URL points at the
  frontend room page with a signed ticket (``django.core.signing``) that the
  room page / websocket gateway can verify with ``verify_join_ticket``.
- ``zoom``: documented stub. Wire it to the Zoom Meeting SDK / JWT signature
  endpoint; until then it raises ``NotImplementedError`` so misconfiguration
  is loud rather than silently insecure.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from django.conf import settings
from django.core import signing
from django.utils import timezone

JOIN_TICKET_SALT = "batches.live-join"


def signed_url_ttl() -> timedelta:
    return timedelta(minutes=int(getattr(settings, "SIGNED_URL_TTL_MINUTES", 30)))


class LiveProvider(Protocol):
    name: str

    def create_join_url(self, session, user) -> tuple[str, object]:
        """Return ``(join_url, expires_at)`` for this user and session."""
        ...


class InternalRoomProvider:
    """
    Built-in browser room. The ticket is a signed payload ``{sid, uid}`` with a
    TTL of ``SIGNED_URL_TTL_MINUTES``; it carries no secret and is useless for
    any other session or user.
    """

    name = "internal"

    def create_join_url(self, session, user):
        expires_at = timezone.now() + signed_url_ttl()
        ticket = signing.dumps({"sid": session.id, "uid": user.id}, salt=JOIN_TICKET_SALT)
        base = settings.PUBLIC_SITE_URL.rstrip("/")
        return f"{base}/student/live-classes/{session.id}/room?ticket={ticket}", expires_at


class ZoomProvider:
    """
    Stub for a real conferencing provider.

    A production implementation would:
    1. Look up ``session.provider_meeting_ref`` (the Zoom meeting id, never exposed).
    2. Register ``user`` as a meeting registrant (or mint a Meeting SDK signature)
       using the server-side API credentials from the environment.
    3. Return the per-registrant join URL plus its expiry.

    It must never return the host key or a static passcode.
    """

    name = "zoom"

    def create_join_url(self, session, user):  # pragma: no cover - documented stub
        raise NotImplementedError(
            "Zoom provider is not configured. Set LIVE_PROVIDER=internal or implement ZoomProvider."
        )


_PROVIDERS: dict[str, type] = {
    InternalRoomProvider.name: InternalRoomProvider,
    ZoomProvider.name: ZoomProvider,
}


def get_provider() -> LiveProvider:
    name = str(getattr(settings, "LIVE_PROVIDER", "internal") or "internal").lower()
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        raise NotImplementedError(f"Unknown LIVE_PROVIDER '{name}'. Known providers: {', '.join(sorted(_PROVIDERS))}.")
    return provider_cls()


def verify_join_ticket(ticket: str) -> dict:
    """Validate an internal-room ticket; raises ``signing.BadSignature`` / ``SignatureExpired``."""
    return signing.loads(ticket, salt=JOIN_TICKET_SALT, max_age=signed_url_ttl())
