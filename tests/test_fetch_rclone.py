import hashlib
from pathlib import Path
import runpy
import urllib.error

import pytest


def test_official_download_fallback_and_integrity():
    script = runpy.run_path(str(Path(__file__).parents[1] / "scripts/fetch_rclone.py"))
    payload = b"test archive"
    calls = []

    def fetch(url):
        calls.append(url)
        if "downloads.rclone.org" in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        if url.endswith("SHA256SUMS"):
            return f"{hashlib.sha256(payload).hexdigest()}  sample.zip\n".encode()
        return payload

    assert script["verified_archive"]("sample", fetch) == payload
    assert len(calls) == 3
    assert all("github.com/rclone/rclone/releases/download/" in url for url in calls[1:])

    def corrupted(url):
        return b"bad  sample.zip\n" if url.endswith("SHA256SUMS") else payload

    with pytest.raises(ValueError, match="checksum mismatch"):
        script["verified_archive"]("sample", corrupted)

    def unavailable(url):
        raise urllib.error.URLError("offline")

    with pytest.raises(urllib.error.URLError):
        script["verified_archive"]("sample", unavailable)
