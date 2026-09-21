"""Client identity is distinct from Codex's shared session transport."""

CODEX_SOURCES = {"codex", "codex-desktop", "vscode-codex"}


def codex_source(session_metadata: dict) -> str:
    originator = str(session_metadata.get("originator") or "").strip().casefold()
    if originator == "codex desktop":
        return "codex-desktop"
    if originator == "codex_vscode":
        return "vscode-codex"
    # Desktop also writes source=vscode. Without an explicit client, stay generic.
    return "codex"
