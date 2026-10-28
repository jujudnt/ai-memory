"""A stable, bounded-memory source copy and verified incremental parser cache."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from collections import OrderedDict
from dataclasses import fields, is_dataclass
from pathlib import Path


class SourceChanged(OSError):
    """The writer changed the source during capture. Retry on the next scan."""


def signature(stat) -> tuple:
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino)


class SourceSnapshot:
    def __init__(self, path: Path, cache: Path, prefix_lengths=()):
        self.stream = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=cache)
        self.prefix_digests = {}
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                descriptor_stat = os.fstat(source.fileno())
                self.stat = path.stat()
                # Windows Python 3.12+ can report change time through fstat
                # and creation time through stat. Compare each clock with its
                # own API, while checking identity and content stamps across both.
                if (descriptor_stat.st_size, descriptor_stat.st_mtime_ns, descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                    self.stat.st_size, self.stat.st_mtime_ns, self.stat.st_dev, self.stat.st_ino
                ):
                    raise SourceChanged(str(path))
                self.size = descriptor_stat.st_size
                boundaries = sorted({n for n in prefix_lengths if isinstance(n, int) and 0 < n < self.size} | {self.size})
                position = 0
                for boundary in boundaries:
                    while position < boundary:
                        chunk = source.read(min(1024 * 1024, boundary - position))
                        if not chunk:
                            raise SourceChanged(str(path))
                        self.stream.write(chunk)
                        digest.update(chunk)
                        position += len(chunk)
                    self.prefix_digests[boundary] = digest.hexdigest()
                if signature(os.fstat(source.fileno())) != signature(descriptor_stat) or signature(path.stat()) != signature(self.stat):
                    raise SourceChanged(str(path))
            self.digest = digest.hexdigest()
            self.stream.seek(max(0, self.size - 1))
            self.complete_line = self.stream.read() == b"\n"
            self.stream.seek(0)
        except BaseException:
            self.stream.close()
            raise

    def chunks(self, offset=0):
        self.stream.seek(offset)
        while chunk := self.stream.read(1024 * 1024):
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()


def _weight(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_weight(k, seen) + _weight(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, set)):
        size += sum(_weight(item, seen) for item in value)
    elif is_dataclass(value):
        size += sum(_weight(getattr(value, field.name), seen) for field in fields(value))
    return size


class ParseCache:
    """Only normalized parser state is retained, bounded to 32 MiB and four sessions."""
    def __init__(self, budget=32 * 1024 * 1024):
        self.budget = budget
        self.entries = OrderedDict()

    def pop(self, key):
        return self.entries.pop(key, None)

    def put(self, key, snapshot, state):
        # A partial last record is reparsed from the start, never silently lost.
        if not snapshot.complete_line or state.get("partial"):
            return
        weight = _weight(state)
        if weight > self.budget:
            return
        while self.entries and (len(self.entries) >= 4 or sum(item[3] for item in self.entries.values()) + weight > self.budget):
            self.entries.popitem(last=False)
        self.entries[key] = (snapshot.size, snapshot.digest, state, weight)
