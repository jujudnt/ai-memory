import json
import sqlite3
from dataclasses import asdict, replace
import pytest

from aimemory.db.sqlite import explicit_title
from test_cloud_integrity import memory, session
from aimemory.state import write_json


def _names(service):
    return {row["source_session_id"]: row["name"] for row in service.list_conversations(limit=50)}


def test_codex_thread_names_follow_renames_without_new_revisions(tmp_path):
    codex = tmp_path / "codex"
    session(codex, "t1", text="premier message")
    session(codex, "t2", text="autre message")
    index = codex / "session_index.jsonl"
    index.write_text(json.dumps({"id": "t1", "thread_name": "Retrouver Sabai"}) + "\n")
    service = memory(tmp_path / "memory")
    service.import_codex(codex)
    assert _names(service) == {"t1": "Retrouver Sabai", "t2": None}
    snapshots = sorted((service.paths.archive).rglob("*.json.*"))

    with index.open("a") as stream:  # Codex appends renames; the last line wins.
        stream.write(json.dumps({"id": "t1", "thread_name": "  Hub   2 "}) + "\n")
    assert service.import_codex(codex).imported == 0
    assert _names(service)["t1"] == "Hub 2"
    assert sorted((service.paths.archive).rglob("*.json.*")) == snapshots
    assert service.search("autre")[0]["name"] is None


def test_only_titles_distinct_from_the_first_message_are_names(tmp_path):
    service = memory(tmp_path / "memory")
    session(tmp_path / "codex", "t1", text="bonjour   le monde")
    service.import_codex(tmp_path / "codex")
    conversation = service.get_conversation(service.list_conversations()[0]["id"])
    assert explicit_title(conversation) is None
    named = replace(conversation, id="claude-1", source="claude-desktop", source_session_id="c1",
                    title="AI Memory interface prototype")
    derived = replace(named, id="claude-2", source_session_id="c2", title="bonjour le monde")
    for item in (named, derived):
        service.db.upsert_conversation(item, service.archive.write(item))
    assert _names(service) == {"t1": None, "c1": "AI Memory interface prototype", "c2": None}


def test_existing_index_is_backfilled_in_small_batches(tmp_path):
    service = memory(tmp_path / "memory")
    session(tmp_path / "codex", "t1")
    service.import_codex(tmp_path / "codex")
    base = service.get_conversation(service.list_conversations()[0]["id"])
    for number in range(3):
        item = replace(base, id=f"claude-{number}", source="claude-desktop",
                       source_session_id=f"c{number}", title=f"Titre {number}")
        service.db.upsert_conversation(item, service.archive.write(item))
    with service.db.connect() as conn:  # Simulate an index built before names existed.
        conn.execute("DELETE FROM conversation_names")
    assert service._repair_conversation_names(batch_size=2) == 2
    assert service._repair_conversation_names(batch_size=2) == 1
    assert service._repair_conversation_names(batch_size=2) == 0
    assert {v for k, v in _names(service).items() if k.startswith("c")} == {"Titre 0", "Titre 1", "Titre 2"}


def test_cloud_names_projects_and_devices_survive_rename_and_rebuild(tmp_path, monkeypatch):
    import aimemory.adapters.codex as codex_module
    codex = tmp_path / "codex-a"
    session(codex, "sabai", text="Projet sur mon autre Mac")
    index = codex / "session_index.jsonl"
    index.write_text(json.dumps({"id": "sabai", "thread_name": "Sabai - application", "updated_at": "2026-09-28T10:00:00Z"}) + "\n")
    with sqlite3.connect(codex / "state_5.sqlite") as conn:
        conn.executescript("CREATE TABLE threads(id TEXT, project_id TEXT, cwd TEXT); "
                           "CREATE TABLE projects(id TEXT,name TEXT,metadata TEXT); "
                           "CREATE TABLE project_roots(project_id TEXT,path TEXT,position INTEGER);")
        conn.execute("INSERT INTO threads VALUES ('sabai','perso','/Users/julia/Sabai')")
        conn.execute("INSERT INTO projects VALUES ('perso','Mes projets',NULL)")
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    for service in (a, b):
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(tmp_path / "cloud")})
    monkeypatch.setattr(codex_module, "_stable_device_id", lambda: "mac-air")
    monkeypatch.setattr(codex_module.socket, "gethostname", lambda: "MacBook Air Julia")
    a.import_codex(codex)
    assert a.sync_now()["status"] == "synced"
    assert b.sync_now()["status"] == "synced"
    row = b.search("Sabai - application")[0]
    assert row["name"] == "Sabai - application"
    assert row["project_name"] == "Mes projets"
    assert row["device_name"] == "MacBook Air Julia"
    assert row["source"] == "codex"
    assert b.db.conversation_page(row["id"])["name"] == "Sabai - application"
    before = {path.relative_to(tmp_path / "cloud") for path in (tmp_path / "cloud/snapshots").rglob("*.json.*")}
    with index.open("a") as stream:
        stream.write(json.dumps({"id": "sabai", "thread_name": "Titre totalement renommé", "updated_at": "2026-09-28T11:00:00Z"}) + "\n")
    with sqlite3.connect(codex / "state_5.sqlite") as conn:
        conn.execute("UPDATE projects SET name='Client Sabai'")
    assert a.import_codex(codex).imported == 0
    a.sync_now()
    b.sync_now()
    assert b.search("totalement renommé")[0]["name"] == "Titre totalement renommé"
    assert b.search("Client Sabai")[0]["project_name"] == "Client Sabai"
    assert before == {path.relative_to(tmp_path / "cloud") for path in (tmp_path / "cloud/snapshots").rglob("*.json.*")}
    inventory = sorted(str(path) for path in (tmp_path / "cloud").rglob("*"))
    a.import_codex(codex)
    a.sync_now()
    assert inventory == sorted(str(path) for path in (tmp_path / "cloud").rglob("*"))
    with b.db.connect() as conn:
        conn.execute("DELETE FROM conversation_metadata")
        conn.execute("DELETE FROM conversation_names")
    b.rebuild_from_archives()
    assert b.list_conversations()[0]["name"] == "Titre totalement renommé"
    # Reindexing an old body must not reset renamed metadata.
    b.index_archive(next((b.paths.archive / "sources").rglob("*.json.*")))
    assert b.list_conversations()[0]["project_name"] == "Client Sabai"
    from aimemory.catalog import publish
    old_body = a.get_conversation(row["id"])
    sibling = replace(old_body, id="sibling", source_session_id="sibling")
    b.accept_conversation(sibling)
    publish(b, "sibling", "project", asdict(old_body.project), 1)
    assert {item["project_name"] for item in b.list_conversations()} == {"Client Sabai"}
    assert len(b.search("Client")) == 2


def test_catalog_converges_and_rejects_corruption(tmp_path):
    from aimemory.catalog import publish
    a, b = memory(tmp_path / "a"), memory(tmp_path / "b")
    for service in (a, b):
        write_json(service.paths.state / "cloud.json", {"provider": "local-folder", "root": str(tmp_path / "cloud")})
    session(tmp_path / "codex")
    a.import_codex(tmp_path / "codex")
    cid = a.list_conversations()[0]["id"]
    publish(a, cid, "name", "Nouveau titre", 2000)
    a.sync_now()
    b.sync_now()
    publish(b, cid, "name", "Ancien titre", 1000)
    b.sync_now()
    a.sync_now()
    assert a.list_conversations()[0]["name"] == b.list_conversations()[0]["name"] == "Nouveau titre"
    victim = next((tmp_path / "cloud/catalog").rglob("*.json"))
    victim.write_bytes(b"{}")
    c = memory(tmp_path / "c")
    write_json(c.paths.state / "cloud.json", {"provider": "local-folder", "root": str(tmp_path / "cloud")})
    with pytest.raises(ValueError, match="checksum"):
        c.sync_now()
