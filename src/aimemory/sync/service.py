from __future__ import annotations

from pathlib import Path

from aimemory.archive import JsonArchive
from aimemory.cloud.local import LocalFolderProvider


class SyncService:
    def __init__(self, archive: JsonArchive, provider: LocalFolderProvider):
        self.archive = archive
        self.provider = provider

    def upload_archives(self) -> dict[str, int]:
        uploaded = 0
        for path in self.archive.iter_archives():
            remote_path = path.relative_to(self.archive.archive_root).as_posix()
            self.provider.upload(path, remote_path)
            uploaded += 1
        return {"uploaded": uploaded}

    def download_archives(self) -> dict[str, int]:
        downloaded = 0
        for obj in self.provider.list_objects():
            if not (obj.remote_path.endswith(".json.zst") or obj.remote_path.endswith(".json.gz")):
                continue
            local_path = Path(self.archive.archive_root) / obj.remote_path
            if local_path.exists() and local_path.stat().st_size == obj.size:
                continue
            self.provider.download(obj.remote_path, local_path)
            downloaded += 1
        return {"downloaded": downloaded}
