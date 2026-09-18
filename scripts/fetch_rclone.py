"""Fetch the pinned, checksum-verified cloud helper for packaging."""
import hashlib
import io
import platform
import urllib.request
import zipfile
from pathlib import Path

VERSION = "v1.75.1"
system = {"Darwin": "osx", "Windows": "windows", "Linux": "linux"}[platform.system()]
arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
stem = f"rclone-{VERSION}-{system}-{arch}"
base = f"https://downloads.rclone.org/{VERSION}"
checksums = urllib.request.urlopen(f"{base}/SHA256SUMS", timeout=60).read().decode()
expected = next(line.split()[0] for line in checksums.splitlines() if line.strip() and line.split()[-1].lstrip("*") == f"{stem}.zip")
payload = urllib.request.urlopen(f"{base}/{stem}.zip", timeout=120).read()
assert hashlib.sha256(payload).hexdigest() == expected, "rclone checksum mismatch"
destination = Path(__file__).resolve().parents[1] / "vendor"
destination.mkdir(exist_ok=True)
name = "rclone.exe" if system == "windows" else "rclone"
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    (destination / name).write_bytes(archive.read(f"{stem}/{name}"))
(destination / "rclone-COPYING.txt").write_bytes(urllib.request.urlopen(
    f"https://raw.githubusercontent.com/rclone/rclone/{VERSION}/COPYING", timeout=60).read())
(destination / name).chmod(0o755)
print(f"Verified rclone {VERSION} for {system}/{arch}")
