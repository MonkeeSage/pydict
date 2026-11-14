"""Writes small StarDict dictionaries for tests."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from pydict.stardict.index import sort_key


def write_dictzip(path: Path, data: bytes, chunk_len: int = 64) -> None:
    chunks = [data[i : i + chunk_len] for i in range(0, len(data), chunk_len)] or [b""]
    comp = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    blobs = []
    for n, chunk in enumerate(chunks):
        flush = zlib.Z_FINISH if n == len(chunks) - 1 else zlib.Z_FULL_FLUSH
        blobs.append(comp.compress(chunk) + comp.flush(flush))
    ra = struct.pack("<HHH", 1, chunk_len, len(blobs)) + b"".join(
        struct.pack("<H", len(b)) for b in blobs
    )
    extra = b"RA" + struct.pack("<H", len(ra)) + ra
    header = b"\x1f\x8b\x08" + bytes([4 | 8]) + b"\0\0\0\0\x02\x03"
    header += struct.pack("<H", len(extra)) + extra + b"test.dict\0"
    trailer = struct.pack("<II", zlib.crc32(data), len(data) & 0xFFFFFFFF)
    path.write_bytes(header + b"".join(blobs) + trailer)


def write_dictionary(
    directory: Path,
    name: str,
    entries: dict[str, str | list[tuple[str, bytes]]],
    bookname: str | None = None,
    sametypesequence: str | None = "m",
    synonyms: dict[str, str] | None = None,
    dictzip: bool = False,
    offset_bits: int = 32,
    chunk_len: int = 64,
) -> Path:
    """Write a dictionary and return its ``.ifo`` path.

    With ``sametypesequence`` entries are strings; without it they are lists
    of ``(type, data)`` fields.
    """
    directory.mkdir(parents=True, exist_ok=True)
    words = sorted(entries, key=lambda w: sort_key(w.encode()))
    data = bytearray()
    idx = bytearray()
    offset_fmt = ">QI" if offset_bits == 64 else ">II"
    for word in words:
        value = entries[word]
        if sametypesequence:
            blob = value.encode() if isinstance(value, str) else value
        else:
            blob = b""
            for kind, field in value:
                blob += kind.encode()
                blob += field + b"\0" if kind.islower() else struct.pack(">I", len(field)) + field
        idx += word.encode() + b"\0" + struct.pack(offset_fmt, len(data), len(blob))
        data += blob
    base = directory / name
    (base.with_name(name + ".idx")).write_bytes(bytes(idx))
    if dictzip:
        write_dictzip(base.with_name(name + ".dict.dz"), bytes(data), chunk_len)
    else:
        base.with_name(name + ".dict").write_bytes(bytes(data))
    lines = [
        "StarDict's dict ifo file",
        "version=3.0.0",
        f"bookname={bookname or name}",
        f"wordcount={len(words)}",
        f"idxfilesize={len(idx)}",
    ]
    if offset_bits == 64:
        lines.append("idxoffsetbits=64")
    if sametypesequence:
        lines.append(f"sametypesequence={sametypesequence}")
    if synonyms:
        syn = bytearray()
        for alias in sorted(synonyms, key=lambda w: sort_key(w.encode())):
            syn += alias.encode() + b"\0" + struct.pack(">I", words.index(synonyms[alias]))
        base.with_name(name + ".syn").write_bytes(bytes(syn))
        lines.append(f"synwordcount={len(synonyms)}")
    ifo = base.with_name(name + ".ifo")
    ifo.write_text("\n".join(lines) + "\n")
    return ifo
