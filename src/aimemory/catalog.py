"""Small, immutable conversation metadata shared independently of message archives."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from aimemory.models import ProjectIdentity
from aimemory.state import atomic_write


def validate_record(data: dict) -> None:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported conversation metadata")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", str(data.get("conversation_id", ""))):
        raise ValueError("Invalid conversation metadata identity")
    stamp = datetime.fromisoformat(data["updated_at"])
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("Conversation metadata must use UTC")
    field, value = data.get("field"), data.get("value")
    if field == "name":
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError("Invalid conversation name")
    elif field == "project":
        if not isinstance(value, dict) or not value.get("id") or not value.get("name"):
            raise ValueError("Invalid conversation project")
        if any(item is not None and (not isinstance(item, str) or len(item) > 4096) for item in value.values()):
            raise ValueError("Invalid project metadata")
        ProjectIdentity(**value)
    else:
        raise ValueError("Invalid conversation metadata field")


def publish(service, conversation_id: str, field: str, value, timestamp: float) -> None:
    record = {"version": 1, "conversation_id": conversation_id, "field": field, "value": value,
              "updated_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="microseconds")}
    validate_record(record)
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    path = service.paths.archive / "catalog" / conversation_id / f"{digest}.json"
    if not path.exists():
        atomic_write(path, payload)
    service.db.apply_catalog(record, digest)
