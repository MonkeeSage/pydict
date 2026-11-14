"""StarDict dictionaries: ``.ifo`` metadata plus ``.idx``/``.dict``/``.syn`` data."""

from __future__ import annotations

import mmap
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from pydict.stardict.dictzip import DictzipReader
from pydict.stardict.index import WordIndex

IFO_MAGIC = "StarDict's dict ifo file"


class DictionaryError(ValueError):
    pass


@dataclass
class DictInfo:
    ifo_path: Path
    bookname: str
    wordcount: int = 0
    synwordcount: int = 0
    idxfilesize: int = 0
    idxoffsetbits: int = 32
    sametypesequence: str = ""
    version: str = ""
    author: str = ""
    email: str = ""
    website: str = ""
    description: str = ""
    date: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def base(self) -> Path:
        return self.ifo_path.with_suffix("")


def parse_ifo(path: str | Path) -> DictInfo:
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        raise DictionaryError(f"{path}: {e.strerror}") from e
    if not lines or lines[0].lstrip("﻿").strip() != IFO_MAGIC:
        raise DictionaryError(f"{path}: not a StarDict .ifo file")
    values: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    if "bookname" not in values:
        raise DictionaryError(f"{path}: missing bookname")

    def num(key: str, default: int = 0) -> int:
        try:
            return int(values.pop(key, default))
        except ValueError:
            return default

    return DictInfo(
        ifo_path=path,
        bookname=values.pop("bookname"),
        wordcount=num("wordcount"),
        synwordcount=num("synwordcount"),
        idxfilesize=num("idxfilesize"),
        idxoffsetbits=num("idxoffsetbits", 32),
        sametypesequence=values.pop("sametypesequence", ""),
        version=values.pop("version", ""),
        author=values.pop("author", ""),
        email=values.pop("email", ""),
        website=values.pop("website", ""),
        description=values.pop("description", ""),
        date=values.pop("date", ""),
        extra=values,
    )


def parse_fields(data: bytes, sametypesequence: str = "") -> list[tuple[str, bytes]]:
    """Split a ``.dict`` entry into ``(type, data)`` fields.

    Lower-case types are NUL-terminated text, upper-case types are binary
    with a 32-bit size prefix. With ``sametypesequence`` the type bytes are
    omitted and the last field has neither terminator nor size.
    """
    fields: list[tuple[str, bytes]] = []
    pos, end = 0, len(data)

    def take(kind: str, last: bool) -> None:
        nonlocal pos
        if last:
            fields.append((kind, data[pos:]))
            pos = end
        elif kind.islower():
            nul = data.find(b"\0", pos)
            nul = end if nul < 0 else nul
            fields.append((kind, data[pos:nul]))
            pos = nul + 1
        else:
            (size,) = struct.unpack_from(">I", data, pos)
            pos += 4
            fields.append((kind, data[pos : pos + size]))
            pos += size

    try:
        if sametypesequence:
            for i, kind in enumerate(sametypesequence):
                take(kind, i == len(sametypesequence) - 1)
        else:
            while pos < end:
                kind = chr(data[pos])
                pos += 1
                take(kind, False)
    except struct.error:
        pass  # truncated entry; keep what we have
    return fields


@dataclass
class Entry:
    word: str
    fields: list[tuple[str, bytes]]
    # The headword this entry was reached through, if found via ``.syn``.
    synonym: str | None = None


def _first_existing(*paths: Path) -> Path | None:
    return next((p for p in paths if p.is_file()), None)


class Dictionary:
    """A StarDict dictionary. Call :meth:`open` before searching."""

    def __init__(self, info: DictInfo) -> None:
        self.info = info
        base = info.base
        name = base.name
        self.idx_path = _first_existing(base.with_name(name + ".idx"), base.with_name(name + ".idx.gz"))
        self.dict_path = _first_existing(base.with_name(name + ".dict"), base.with_name(name + ".dict.dz"))
        self.syn_path = _first_existing(base.with_name(name + ".syn"))
        if self.idx_path is None:
            raise DictionaryError(f"{info.ifo_path}: no .idx file")
        if self.dict_path is None:
            raise DictionaryError(f"{info.ifo_path}: no .dict or .dict.dz file")
        self.idx: WordIndex | None = None
        self.syn: WordIndex | None = None
        self._dz: DictzipReader | None = None
        self._dict_file = None
        self._dict_map: mmap.mmap | bytes | None = None

    @classmethod
    def from_ifo(cls, path: str | Path) -> Dictionary:
        return cls(parse_ifo(path))

    @property
    def name(self) -> str:
        return self.info.bookname

    @property
    def is_open(self) -> bool:
        return self.idx is not None

    def open(self, cache_dir: Path | None = None) -> None:
        if self.is_open:
            return
        if self.info.idxoffsetbits not in (32, 64):
            raise DictionaryError(f"{self.info.ifo_path}: bad idxoffsetbits")
        self._offset_fmt = ">QI" if self.info.idxoffsetbits == 64 else ">II"
        try:
            self._open_files(cache_dir)
        except BaseException:
            self.close()
            raise

    def _open_files(self, cache_dir: Path | None) -> None:
        trailer = struct.calcsize(self._offset_fmt)
        if self.dict_path.suffix == ".dz":
            self._dz = DictzipReader(self.dict_path)
        elif self.dict_path.stat().st_size:
            self._dict_file = open(self.dict_path, "rb")
            self._dict_map = mmap.mmap(self._dict_file.fileno(), 0, access=mmap.ACCESS_READ)
        else:
            self._dict_map = b""
        if self.syn_path is not None:
            self.syn = WordIndex(self.syn_path, 4, cache_dir)
        self.idx = WordIndex(self.idx_path, trailer, cache_dir)

    def close(self) -> None:
        for index in (self.idx, self.syn):
            if index is not None:
                index.close()
        self.idx = self.syn = None
        if self._dz is not None:
            self._dz.close()
            self._dz = None
        if isinstance(self._dict_map, mmap.mmap):
            self._dict_map.close()
        if self._dict_file is not None:
            self._dict_file.close()
        self._dict_map = self._dict_file = None

    def _read(self, offset: int, size: int) -> bytes:
        if self._dz is not None:
            try:
                return self._dz.read(offset, size)
            except zlib.error as e:
                raise DictionaryError(f"{self.dict_path}: corrupt data ({e})") from e
        return bytes(self._dict_map[offset : offset + size])

    def entry(self, i: int, synonym: str | None = None) -> Entry:
        assert self.idx is not None
        offset, size = struct.unpack(self._offset_fmt, self.idx.trailer_bytes(i))
        data = self._read(offset, size)
        return Entry(self.idx.word(i), parse_fields(data, self.info.sametypesequence), synonym)

    def prefix(self, prefix: str, limit: int) -> list[str]:
        """Headwords and synonyms starting with ``prefix`` (ASCII case-insensitive)."""
        assert self.idx is not None
        words = [self.idx.word(i) for i in self.idx.prefix(prefix, limit)]
        if self.syn is not None:
            words += [self.syn.word(i) for i in self.syn.prefix(prefix, limit)]
        return words

    def find_words(self, word: str) -> list[str]:
        """Headwords and synonyms equal to ``word`` ignoring ASCII case."""
        assert self.idx is not None
        words = [self.idx.word(i) for i in self.idx.find(word, ignore_case=True)]
        if self.syn is not None:
            words += [self.syn.word(i) for i in self.syn.find(word, ignore_case=True)]
        return words

    def lookup(self, word: str) -> list[Entry]:
        """Entries for exactly ``word``, including ones reached via synonyms."""
        assert self.idx is not None
        indexes = self.idx.find(word)
        entries = [self.entry(i) for i in indexes]
        if self.syn is not None:
            seen = set(indexes)
            for s in self.syn.find(word):
                (target,) = struct.unpack(">I", self.syn.trailer_bytes(s))
                if target < len(self.idx) and target not in seen:
                    seen.add(target)
                    entries.append(self.entry(target, synonym=word))
        return entries

    def __repr__(self) -> str:
        return f"Dictionary({self.name!r}, {str(self.info.ifo_path)!r})"
