from __future__ import annotations

import hashlib
import re
from pathlib import Path

from aimemory.models import ProjectIdentity


def normalize_git_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    value = remote.strip()
    if not value:
        return None

    value = value.removesuffix(".git")

    ssh_match = re.match(r"^git@([^:]+):(.+)$", value)
    if ssh_match:
        host, path = ssh_match.groups()
        return f"{host.lower()}/{path.strip('/').lower()}"

    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^ssh://git@", "", value)
    value = value.strip("/")
    return value.lower() or None


def stable_project_id(name: str, git_remote: str | None = None) -> str:
    seed = git_remote or name
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"project_{digest}"


def identify_project(
    cwd: str | None,
    git_remote: str | None = None,
    git_branch: str | None = None,
) -> ProjectIdentity | None:
    normalized_remote = normalize_git_remote(git_remote)
    if normalized_remote:
        name = normalized_remote.rstrip("/").split("/")[-1]
        return ProjectIdentity(
            id=stable_project_id(name, normalized_remote),
            name=name,
            cwd=cwd,
            git_remote=normalized_remote,
            git_branch=git_branch,
        )
    if cwd:
        name = Path(cwd).name or "unknown-project"
        return ProjectIdentity(
            id=stable_project_id(str(Path(cwd))),
            name=name,
            cwd=cwd,
            git_remote=None,
            git_branch=git_branch,
        )
    return None
