import hashlib
import json
import os

import pytest

from aimemory.archive.raw_backup import read_raw, write_raw


def test_append_deltas_reconstruct_exact_bytes_without_recopying_full_history(tmp_path):
    raw = os.urandom(128 * 1024)
    previous = {}
    saved = []
    for tail in (b"", b"first new record\n", b"second new record\n"):
        raw += tail
        digest = hashlib.sha256(raw).hexdigest()
        path = write_raw(tmp_path, "session-1", raw, digest, previous)
        previous = {"raw_path": str(path), "sha256": digest, "size": len(raw)}
        saved.append(path)
        assert read_raw(tmp_path, str(path)) == raw
    assert str(saved[1]).endswith(".delta.json.gz")
    assert (tmp_path / saved[1]).stat().st_size < 1024
    assert (tmp_path / saved[2]).stat().st_size < 1024
    (tmp_path / saved[0]).unlink()
    with pytest.raises(FileNotFoundError):
        read_raw(tmp_path, str(saved[-1]))


def test_source_rewrite_creates_independent_full_backup(tmp_path):
    digest = hashlib.sha256(b"original").hexdigest()
    first = write_raw(tmp_path, "session", b"original", digest, {})
    previous = {"raw_path": str(first), "sha256": digest, "size": 8}
    rewritten = b"rewritten source"
    path = write_raw(tmp_path, "session", rewritten, hashlib.sha256(rewritten).hexdigest(), previous)
    assert str(path).endswith(".jsonl.gz")
    assert read_raw(tmp_path, str(path)) == rewritten


def test_raw_chain_rejects_path_escape(tmp_path):
    with pytest.raises(ValueError, match="Invalid raw"):
        read_raw(tmp_path, "../../private.gz")
