"""
Prefixed, URL-safe, non-sequential primary keys (e.g. ``crs_01HZX...``).

Non-guessable ids prevent enumeration of other users' records and match the
id format the frontend already expects (string ids with a resource prefix).
"""

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (no I, L, O, U)


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def make_id(prefix: str) -> str:
    """Time-ordered 26-char identifier (48-bit ms timestamp + 80 random bits), ULID-like."""
    ts = int(time.time() * 1000)
    rand = secrets.randbits(80)
    return f"{prefix}_{_encode(ts, 10)}{_encode(rand, 16)}"


def id_factory(prefix: str):
    """Return a zero-argument callable usable as a model field default."""

    def _default() -> str:
        return make_id(prefix)

    _default.__qualname__ = f"id_factory_{prefix}"
    return _default
