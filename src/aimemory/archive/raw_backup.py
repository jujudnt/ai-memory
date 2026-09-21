from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

from aimemory.state import atomic_write


RAW_PATTERN = r"raw/[a-zA-Z0-9_-]+/[a-f0-9]{64}\.(?:jsonl|delta\.json)\.gz"


def write_raw(root: Path, conversation_id: str, raw: bytes, digest: str, previous: dict) -> Path:
    path = Path("raw") / conversation_id / f"{digest}.jsonl.gz"
    length = previous.get("size", 0)
    parent = Path(previous.get("raw_path", "")).as_posix()
    if (length and length < len(raw) and re.fullmatch(RAW_PATTERN, parent)
            and ((root / parent).is_file() or previous.get("raw_backed_up"))
            and previous.get("raw_depth", 0) < 32
            and hashlib.sha256(raw[:length]).hexdigest() == previous.get("sha256")):
        path = path.with_name(f"{digest}.delta.json.gz")
        payload = json.dumps({"parent": parent, "prefix_size": length,
                              "append": base64.b64encode(raw[length:]).decode("ascii")}).encode()
    else:
        payload = raw
    if not (root / path).exists():
        atomic_write(root / path, gzip.compress(payload, compresslevel=6, mtime=0))
    return path


def read_raw(root: Path, relative: str) -> bytes:
    relative = Path(relative).as_posix()
    parts = []
    seen = set()
    expected_length = None
    final_digest = Path(relative).name.split(".")[0]
    while True:
        if not re.fullmatch(RAW_PATTERN, relative) or relative in seen:
            raise ValueError("Invalid raw backup chain")
        seen.add(relative)
        payload = gzip.decompress((root / relative).read_bytes())
        if relative.endswith(".jsonl.gz"):
            if expected_length is not None and len(payload) != expected_length:
                raise ValueError("Raw backup prefix length mismatch")
            parts.append(payload)
            break
        delta = json.loads(payload)
        tail = base64.b64decode(delta["append"], validate=True)
        prefix_length = delta["prefix_size"]
        if not isinstance(prefix_length, int) or prefix_length < 0:
            raise ValueError("Invalid raw backup prefix length")
        if expected_length is not None and prefix_length + len(tail) != expected_length:
            raise ValueError("Raw backup delta length mismatch")
        expected_length = prefix_length
        parts.append(tail)
        relative = delta["parent"]
    raw = b"".join(reversed(parts))
    if hashlib.sha256(raw).hexdigest() != final_digest:
        raise ValueError("Raw backup checksum mismatch")
    return raw


class RawIntegrityError(ValueError):
    def __init__(self, relative: str):
        super().__init__("Raw backup checksum or format mismatch")
        self.relative = relative


def load_delta(root: Path, relative: str) -> dict:
    try:
        with gzip.open(root / relative, "rb") as stream:
            delta = json.load(stream)
        if (not isinstance(delta, dict) or not isinstance(delta.get("parent"), str)
                or not re.fullmatch(RAW_PATTERN, delta["parent"])
                or not isinstance(delta.get("prefix_size"), int) or delta["prefix_size"] < 0
                or not isinstance(delta.get("append"), str)):
            raise ValueError("Invalid delta")
        return delta
    except (gzip.BadGzipFile, EOFError, ValueError) as exc:
        raise RawIntegrityError(relative) from exc


def validate_raw(root: Path, relative: str, verified: dict, heartbeat=lambda: None) -> None:
    """Hash each prefix once per sync without materializing full histories."""
    pending = []
    seen = set()
    current = Path(relative).as_posix()
    while current not in verified:
        heartbeat()
        if not re.fullmatch(RAW_PATTERN, current) or current in seen:
            raise ValueError("Invalid raw backup chain")
        seen.add(current)
        if current.endswith(".jsonl.gz"):
            digest = hashlib.sha256()
            length = 0
            try:
                with gzip.open(root / current, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        length += len(chunk)
                        heartbeat()
            except (gzip.BadGzipFile, EOFError) as exc:
                raise RawIntegrityError(current) from exc
            if digest.hexdigest() != Path(current).name.split(".")[0]:
                raise RawIntegrityError(current)
            verified[current] = (digest, length)
            break
        delta = load_delta(root, current)
        pending.append((current, delta))
        current = delta["parent"]
    for name, delta in reversed(pending):
        heartbeat()
        digest, length = verified[delta["parent"]]
        if length != delta["prefix_size"]:
            raise RawIntegrityError(name)
        try:
            tail = base64.b64decode(delta["append"], validate=True)
        except ValueError as exc:
            raise RawIntegrityError(name) from exc
        digest = digest.copy()
        digest.update(tail)
        if digest.hexdigest() != Path(name).name.split(".")[0]:
            raise RawIntegrityError(name)
        verified[name] = (digest, length + len(tail))
