from __future__ import annotations

import gzip
import json
from pathlib import Path

from aimemory.models import NormalizedConversation

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
            path.write_bytes(compressor.compress(payload))
        else:
            with gzip.open(path, "wb") as handle:
                handle.write(payload)
        return path

    def read(self, path: Path) -> NormalizedConversation:
        if path.suffix == ".zst":
            if not zstd:
                raise RuntimeError("Install ai-memory[zstd] to read .zst archives")
            decompressor = zstd.ZstdDecompressor()
            raw = decompressor.decompress(path.read_bytes())
        else:
            with gzip.open(path, "rb") as handle:
                raw = handle.read()
        return NormalizedConversation.from_dict(json.loads(raw.decode("utf-8")))

    def iter_archives(self) -> list[Path]:
        root = self.archive_root / "sources"
        if not root.exists():
            return []
        return sorted([*root.rglob("*.json.zst"), *root.rglob("*.json.gz")])
