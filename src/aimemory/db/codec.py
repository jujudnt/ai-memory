from __future__ import annotations

import zlib


def pack_text(value: str | None) -> str | bytes | None:
    if value is None or len(value) < 1024:
        return value
    raw = value.encode("utf-8")
    compressed = zlib.compress(raw)
    return compressed if len(compressed) < len(raw) else value


def unpack_text(value: str | bytes | None) -> str | None:
    # SQLite distinguishes legacy TEXT from new compressed BLOBs without a sentinel.
    if isinstance(value, bytes):
        return zlib.decompress(value).decode("utf-8")
    return value
