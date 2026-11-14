"""Random access reader for dictzip (``.dict.dz``) files.

A dictzip file is a gzip file whose deflate stream is flushed at fixed
uncompressed intervals ("chunks"). The gzip header carries an ``RA`` extra
field listing the compressed size of every chunk, so any byte range can be
read by inflating only the chunks that cover it.

Files without the ``RA`` field are plain gzip and get decompressed whole.
"""

from __future__ import annotations

import gzip
import mmap
import struct
import zlib
from collections import OrderedDict
from pathlib import Path

_FTEXT, _FHCRC, _FEXTRA, _FNAME, _FCOMMENT = 1, 2, 4, 8, 16


class DictzipError(ValueError):
    pass


class DictzipReader:
    """Reads byte ranges of the uncompressed data in a dictzip file."""

    def __init__(self, path: str | Path, cache_chunks: int = 32) -> None:
        self.path = Path(path)
        self._file = open(self.path, "rb")
        try:
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            self._file.close()
            raise DictzipError(f"{self.path}: empty file") from None
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_size = cache_chunks
        self._whole: bytes | None = None
        self.chunk_len = 0
        self._offsets: list[int] = []
        try:
            self._parse_header()
        except BaseException:
            self.close()
            raise

    def _parse_header(self) -> None:
        m = self._map
        if len(m) < 10 or m[0:2] != b"\x1f\x8b" or m[2] != 8:
            raise DictzipError(f"{self.path}: not a gzip file")
        flags = m[3]
        pos = 10
        ra: tuple[int, list[int]] | None = None
        if flags & _FEXTRA:
            (xlen,) = struct.unpack_from("<H", m, pos)
            pos += 2
            end = pos + xlen
            while pos + 4 <= end:
                si = bytes(m[pos : pos + 2])
                (sublen,) = struct.unpack_from("<H", m, pos + 2)
                if si == b"RA":
                    _ver, chlen, chcnt = struct.unpack_from("<HHH", m, pos + 4)
                    sizes = list(struct.unpack_from(f"<{chcnt}H", m, pos + 10))
                    ra = (chlen, sizes)
                pos += 4 + sublen
            pos = end
        if flags & _FNAME:
            pos = m.find(b"\0", pos) + 1
        if flags & _FCOMMENT:
            pos = m.find(b"\0", pos) + 1
        if flags & _FHCRC:
            pos += 2

        if ra is None:
            # Plain gzip: no random access possible.
            self._whole = gzip.decompress(self._map[:])
            return
        self.chunk_len, sizes = ra
        offsets = [pos]
        for size in sizes:
            offsets.append(offsets[-1] + size)
        self._offsets = offsets

    def _chunk(self, index: int) -> bytes:
        data = self._cache.get(index)
        if data is not None:
            self._cache.move_to_end(index)
            return data
        start, end = self._offsets[index], self._offsets[index + 1]
        data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(self._map[start:end])
        self._cache[index] = data
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return data

    def read(self, offset: int, size: int) -> bytes:
        if self._whole is not None:
            return self._whole[offset : offset + size]
        if size <= 0:
            return b""
        first = offset // self.chunk_len
        last = min((offset + size - 1) // self.chunk_len, len(self._offsets) - 2)
        data = b"".join(self._chunk(i) for i in range(first, last + 1))
        start = offset - first * self.chunk_len
        return data[start : start + size]

    def close(self) -> None:
        self._cache.clear()
        self._map.close()
        self._file.close()
