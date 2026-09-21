from dataclasses import replace

import pytest

from aimemory.adapters.codex import CodexAdapter
from aimemory.sources import codex_source
from aimemory.state import read_json
from test_cloud_integrity import memory, session


@pytest.mark.parametrize("originator,expected", [
    ("Codex Desktop", "codex-desktop"),
    ("codex_vscode", "vscode-codex"),
    (None, "codex"),
    ("unknown", "codex"),
])
def test_shared_vscode_transport_does_not_identify_client(originator, expected):
    assert codex_source({"originator": originator, "source": "vscode"}) == expected


def test_desktop_originator_wins_over_thread_database(tmp_path):
    import json
    path = session(tmp_path / "codex")
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    header["payload"].update(originator="Codex Desktop", source="vscode")
    lines[0] = json.dumps(header)
    path.write_text("\n".join(lines) + "\n")
    adapter = CodexAdapter(tmp_path / "codex")
    adapter.thread_metadata[str(path)] = {"source": "vscode"}
    result = adapter.parse_session(path)
    assert result.source == "codex-desktop"


def test_existing_cloud_only_labels_repaired_without_new_archives(tmp_path):
    service = memory(tmp_path / "memory")
    path = session(tmp_path / "source")
    parsed = CodexAdapter(tmp_path / "source").parse_session(path)
    old = replace(parsed, source="vscode-codex", metadata={
        **parsed.metadata, "session_metadata": {"originator": "Codex Desktop", "source": "vscode"},
    })
    archive = service.archive.write(old)
    service.db.upsert_conversation(old, archive)
    before = {p.relative_to(service.paths.archive): p.read_bytes()
              for p in service.paths.archive.rglob("*") if p.is_file()}
    # No source files on this machine: the repair must use saved archive metadata.
    service.import_codex(tmp_path / "empty")
    assert service.db.list_conversations()[0]["source"] == "codex-desktop"
    assert service.get_conversation(old.id).source == "codex-desktop"
    assert service.db.search("hello", source="vscode") == []
    assert len(service.db.list_conversations(source="codex")) == 1
    assert read_json(service.paths.state / "codex-client-labels-v1.json")["corrected"] == 1
    after = {p.relative_to(service.paths.archive): p.read_bytes()
             for p in service.paths.archive.rglob("*") if p.is_file()}
    assert before == after
