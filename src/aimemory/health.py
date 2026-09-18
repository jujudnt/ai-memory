from datetime import UTC, datetime


def watcher_health(watcher: dict | None, now: datetime | None = None) -> dict:
    """A live process is not enough: also require recent collection activity."""
    watcher = watcher or {}
    if not watcher.get("running"):
        return {"state": "stopped", "label": "Collecte inactive"}
    if watcher.get("last_error"):
        return {"state": "error", "label": "Collecte en erreur"}
    stamp = watcher.get("last_success_at") or watcher.get("started_at")
    try:
        last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = ((now or datetime.now(UTC)) - last.astimezone(UTC)).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return {"state": "starting", "label": "En attente du premier scan"}
    if age > 120:
        return {"state": "stale", "label": "Collecte \u00e0 v\u00e9rifier"}
    if not watcher.get("last_success_at"):
        return {"state": "starting", "label": "Premier scan en cours"}
    return {"state": "running", "label": "Collecte en continu"}
