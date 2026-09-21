from __future__ import annotations

import gzip
import json
import hashlib
import re
from pathlib import Path

from aimemory.models import NormalizedConversation
from aimemory.state import atomic_write
from aimemory.sources import CODEX_SOURCES, codex_source
from aimemory.adapters.codex_messages import normalize_codex_messages

try:
    import zstandard as zstd
except Exception:  # pragma: no cover - depends on optional local package
    zstd = None


class JsonArchive:
    def __init__(self, archive_root: Path):
        self.archive_root = archive_root

    @property
    def extension(self) -> str:
        return ".json.zst" if zstd else ".json.gz"

    def path_for(self, conversation: NormalizedConversation) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", conversation.source) or not re.fullmatch(r"[a-zA-Z0-9_-]+", conversation.id):
            raise ValueError("Invalid archive identity")
        return (
            self.archive_root
            / "sources"
            / conversation.source
            / "sessions"
            / f"{conversation.id}{self.extension}"
        )

    def write(self, conversation: NormalizedConversation) -> Path:
        path = self.path_for(conversation)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(conversation.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        if zstd:
            compressor = zstd.ZstdCompressor(level=9)
            compressed = compressor.compress(payload)
        else:
            compressed = gzip.compress(payload, mtime=0)
        # Retain immutable revisions so simultaneous device edits cannot destroy history.
        if path.exists():
            self.preserve(path, conversation.id)
        atomic_write(path, compressed)
        self.preserve(path, conversation.id)
        return path

    def preserve(self, path: Path, conversation_id: str) -> Path:
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target = self.archive_root / "snapshots" / conversation_id / f"{digest}{''.join(path.suffixes)}"
        if not target.exists():
            atomic_write(target, payload)
        return target

    def read(self, path: Path, normalize: bool = True) -> NormalizedConversation:
        if path.suffix == ".zst":
            if not zstd:
                raise RuntimeError("Install ai-memory[zstd] to read .zst archives")
            decompressor = zstd.ZstdDecompressor()
            raw = decompressor.decompress(path.read_bytes())
        else:
            with gzip.open(path, "rb") as handle:
                raw = handle.read()
        conversation = NormalizedConversation.from_dict(json.loads(raw.decode("utf-8")))
        if conversation.source in CODEX_SOURCES:
            conversation.source = codex_source(conversation.metadata.get("session_metadata") or {})
            if normalize:
                normalize_codex_messages(conversation)
        return conversation

    def iter_archives(self) -> list[Path]:
        root = self.archive_root / "sources"
        if not root.exists():
            return []
        return sorted([*root.rglob("*.json.zst"), *root.rglob("*.json.gz")])
