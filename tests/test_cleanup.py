from aimemory.cleanup import run_auto_cleanup
from aimemory.config import AppPaths


def paths(root):
    result = AppPaths(root, *(root / name for name in ("archive", "db", "vectors", "cache", "logs", "state")))
    result.ensure()
    return result


def test_auto_cleanup_removes_release_artifacts_but_keeps_exchange(tmp_path):
    app_paths = paths(tmp_path / "home")
    (app_paths.cache / "release-v0.3.3").mkdir()
    (app_paths.cache / "release-v0.3.3" / "ai-memory-macos.zip").write_bytes(b"x" * 10)
    (app_paths.cache / "replaced-before-v0.3.3.app").mkdir()
    (app_paths.cache / "replaced-before-v0.3.3.app" / "binary").write_bytes(b"x" * 5)
    (app_paths.cache / "exchange").mkdir()
    (app_paths.cache / "exchange" / "snapshot").write_bytes(b"keep")

    status = run_auto_cleanup(app_paths, force=True)

    assert status["freed_bytes"] == 15
    assert not (app_paths.cache / "release-v0.3.3").exists()
    assert not (app_paths.cache / "replaced-before-v0.3.3.app").exists()
    assert (app_paths.cache / "exchange" / "snapshot").exists()
