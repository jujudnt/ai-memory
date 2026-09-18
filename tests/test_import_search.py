import json

from aimemory.config import AppPaths
from aimemory.service import MemoryService


def test_import_codex_and_search(tmp_path):
    codex_home = tmp_path / "codex"
    session = codex_home / "sessions" / "2026" / "09" / "18" / "session.jsonl"
    session.parent.mkdir(parents=True)
    records = [
        {
            "timestamp": "2026-09-18T00:00:00Z",
            "ordinal": 0,
            "type": "session_meta",
            "payload": {"session_id": "abc", "cwd": "/repo/ai-memory"},
        },
        {
            "timestamp": "2026-09-18T00:01:00Z",
            "ordinal": 1,
            "type": "response_item",
            "payload": {
                "type": "message",
                "id": "m1",
                "role": "user",
                "content": [{"type": "input_text", "text": "We configured Cloud Armor for an endpoint"}],
            },
        },
    ]
    session.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")

    paths = AppPaths(
        home=tmp_path / "home",
        archive=tmp_path / "home" / "archive",
        db=tmp_path / "home" / "db",
        vectors=tmp_path / "home" / "vectors",
        cache=tmp_path / "home" / "cache",
        logs=tmp_path / "home" / "logs",
        state=tmp_path / "home" / "state",
    )
    service = MemoryService(paths)

    result = service.import_codex(codex_home=codex_home)
    rows = service.search("Cloud Armor")

    assert result.scanned == 1
    assert result.imported == 1
    assert len(rows) == 1
    assert rows[0]["source_session_id"] == "abc"
    assert service.status()["archive_count"] == 1
