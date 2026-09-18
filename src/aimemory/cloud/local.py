from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class StorageObject:
    remote_path: str
    size: int
    mtime: float


class LocalFolderProvider:
    name = "local-folder"

    def __init__(self, root: Path):
        self.root = root

    def connect(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def list_objects(self) -> list[StorageObject]:
        self.connect()
        objects: list[StorageObject] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                objects.append(
                    StorageObject(
                        remote_path=path.relative_to(self.root).as_posix(),
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                    )
                )
        return objects

    def upload(self, local_path: Path, remote_path: str) -> None:
        self.connect()
        destination = self.root / remote_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination)

    def download(self, remote_path: str, local_path: Path) -> None:
        source = self.root / remote_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, local_path)

    def delete(self, remote_path: str) -> None:
        path = self.root / remote_path
        if path.exists():
            path.unlink()

    def status(self) -> dict[str, str]:
        return {"provider": self.name, "root": str(self.root)}
