"""Fetch the pinned, checksum-verified cloud helper for packaging."""
import hashlib
import io
import platform
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VERSION = "v1.75.1"


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AI-Memory-build"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def verified_archive(stem, fetch=download):
    bases = [f"https://downloads.rclone.org/{VERSION}",
             f"https://github.com/rclone/rclone/releases/download/{VERSION}"]
    for index, base in enumerate(bases):
        try:
            checksums = fetch(f"{base}/SHA256SUMS").decode()
            expected = next(line.split()[0] for line in checksums.splitlines()
                            if line.strip() and line.split()[-1].lstrip("*") == f"{stem}.zip")
            payload = fetch(f"{base}/{stem}.zip")
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if index == len(bases) - 1:
                raise
            print("Primary rclone download unavailable; trying the official GitHub release.")
            continue
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("rclone checksum mismatch")
        return payload


def main():
    system = {"Darwin": "osx", "Windows": "windows", "Linux": "linux"}[platform.system()]
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
    stem = f"rclone-{VERSION}-{system}-{arch}"
    payload = verified_archive(stem)
    destination = Path(__file__).resolve().parents[1] / "vendor"
    destination.mkdir(exist_ok=True)
    name = "rclone.exe" if system == "windows" else "rclone"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        (destination / name).write_bytes(archive.read(f"{stem}/{name}"))
    (destination / "rclone-COPYING.txt").write_bytes(download(
        f"https://raw.githubusercontent.com/rclone/rclone/{VERSION}/COPYING"))
    (destination / name).chmod(0o755)
    print(f"Verified rclone {VERSION} for {system}/{arch}")


if __name__ == "__main__":
    main()
