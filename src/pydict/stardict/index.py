"""Sorted word lists from ``.idx`` and ``.syn`` files.

Both files are sequences of ``word\\0`` followed by a fixed-size binary
trailer, sorted with StarDict's comparison (ASCII case-insensitive, then
byte-wise). The file is memory-mapped and only an array of entry offsets is
kept in memory, so lookups are a binary search. Building the offset array for
a big dictionary takes a few seconds, so it is cached on disk.
"""

from __future__ import annotations

import gzip
import hashlib
import mmap
import os
import struct
import sys
from array import array
from pathlib import Path

_CACHE_MAGIC = b"PYDICTX1"
_CACHE_HEADER = struct.Struct("<8sQQII")  # magic, size, mtime_ns, trailer, count
# Index files smaller than this are scanned on every start instead of cached.
CACHE_MIN_SIZE = 1 << 20


def sort_key(word: bytes) -> tuple[bytes, bytes]:
    """StarDict's ``stardict_strcmp`` as a sort key.

    ``bytes.lower`` only folds ASCII, matching ``g_ascii_strcasecmp``.
    """
    return word.lower(), word


def default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "pydict"


def _offset_array() -> array:
    return array("I") if array("I").itemsize == 4 else array("L")


def _scan(data: bytes | mmap.mmap, trailer: int) -> array:
    offsets = _offset_array()
    append = offsets.append
    find = data.find
    pos, end = 0, len(data)
    while pos < end:
        nul = find(b"\0", pos)
        if nul < 0 or nul + 1 + trailer > end:
            break  # truncated final entry
        append(pos)
        pos = nul + 1 + trailer
    return offsets


class WordIndex:
    """A memory-mapped, binary-searchable StarDict word list."""

    def __init__(self, path: str | Path, trailer: int, cache_dir: Path | None = None) -> None:
        self.path = Path(path)
        self.trailer = trailer
        self._file = None
        self._map: mmap.mmap | None = None
        if self.path.suffix == ".gz":
            self.data: bytes | mmap.mmap = gzip.decompress(self.path.read_bytes())
        elif self.path.stat().st_size == 0:
            self.data = b""
        else:
            self._file = open(self.path, "rb")
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            self.data = self._map
        self.offsets = self._load_offsets(cache_dir)

    # -- offset cache -------------------------------------------------------

    def _cache_file(self, cache_dir: Path) -> Path:
        digest = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:16]
        return cache_dir / f"{self.path.name}.{digest}.offsets"

    def _load_offsets(self, cache_dir: Path | None) -> array:
        st = self.path.stat()
        if cache_dir is None or st.st_size < CACHE_MIN_SIZE:
            return _scan(self.data, self.trailer)
        cache = self._cache_file(cache_dir)
        try:
            with open(cache, "rb") as f:
                magic, size, mtime, trailer, count = _CACHE_HEADER.unpack(
                    f.read(_CACHE_HEADER.size)
                )
                if (magic, size, mtime, trailer) == (
                    _CACHE_MAGIC, st.st_size, st.st_mtime_ns, self.trailer,
                ):
                    offsets = _offset_array()
                    offsets.frombytes(f.read())
                    if len(offsets) == count:
                        return offsets
        except (OSError, struct.error, ValueError):
            pass
        offsets = _scan(self.data, self.trailer)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
            with open(tmp, "wb") as f:
                f.write(_CACHE_HEADER.pack(
                    _CACHE_MAGIC, st.st_size, st.st_mtime_ns, self.trailer, len(offsets)
                ))
                offsets.tofile(f)
            os.replace(tmp, cache)
        except OSError:
            pass
        return offsets

    # -- access -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.offsets)

    def word_bytes(self, i: int) -> bytes:
        start = self.offsets[i]
        return self.data[start : self.data.find(b"\0", start)]

    def word(self, i: int) -> str:
        return self.word_bytes(i).decode("utf-8", "replace")

    def trailer_bytes(self, i: int) -> bytes:
        start = self.data.find(b"\0", self.offsets[i]) + 1
        return self.data[start : start + self.trailer]

    def lower_bound(self, folded: bytes) -> int:
        """First entry whose ASCII-folded word is >= ``folded``."""
        lo, hi = 0, len(self.offsets)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.word_bytes(mid).lower() < folded:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def prefix(self, prefix: str, limit: int) -> list[int]:
        """Entries whose word starts with ``prefix``, ignoring ASCII case."""
        folded = prefix.encode("utf-8").lower()
        out = []
        i, n = self.lower_bound(folded), len(self.offsets)
        while i < n and len(out) < limit and self.word_bytes(i).lower().startswith(folded):
            out.append(i)
            i += 1
        return out

    def find(self, word: str, ignore_case: bool = False) -> list[int]:
        """Entries whose word equals ``word`` (exactly, or ignoring ASCII case)."""
        raw = word.encode("utf-8")
        folded = raw.lower()
        out = []
        i, n = self.lower_bound(folded), len(self.offsets)
        while i < n:
            w = self.word_bytes(i)
            if w.lower() != folded:
                break
            if ignore_case or w == raw:
                out.append(i)
            i += 1
        return out

    def close(self) -> None:
        self.data = b""
        if self._map is not None:
            self._map.close()
        if self._file is not None:
            self._file.close()
