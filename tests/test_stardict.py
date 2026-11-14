from __future__ import annotations

import gzip
import os
import random
from pathlib import Path

import pytest

from conftest import FRUIT
from pydict.stardict import Dictionary, DictionaryError, Library, parse_fields, parse_ifo
from pydict.stardict import index as index_module
from pydict.stardict.dictzip import DictzipReader
from pydict.stardict.library import edit_distance
from stardict_writer import write_dictionary, write_dictzip


def test_dictzip_random_access(tmp_path: Path) -> None:
    data = bytes(random.Random(1).randrange(256) for _ in range(5000))
    path = tmp_path / "x.dict.dz"
    write_dictzip(path, data, chunk_len=100)
    reader = DictzipReader(path, cache_chunks=2)
    assert reader.chunk_len == 100
    rng = random.Random(2)
    for _ in range(200):
        offset = rng.randrange(len(data))
        size = rng.randrange(400)
        assert reader.read(offset, size) == data[offset : offset + size]
    assert reader.read(4990, 100) == data[4990:]
    reader.close()


def test_dictzip_plain_gzip_fallback(tmp_path: Path) -> None:
    path = tmp_path / "x.dict.dz"
    path.write_bytes(gzip.compress(b"hello world"))
    assert DictzipReader(path).read(6, 5) == b"world"


def test_parse_ifo(fruit_ifo: Path) -> None:
    info = parse_ifo(fruit_ifo)
    assert info.bookname == "Fruit"
    assert info.wordcount == len(FRUIT)
    assert info.sametypesequence == "m"
    assert info.synwordcount == 1


def test_parse_ifo_rejects_other_files(tmp_path: Path) -> None:
    path = tmp_path / "bad.ifo"
    path.write_text("something else\nbookname=x\n")
    with pytest.raises(DictionaryError):
        parse_ifo(path)


def test_parse_fields() -> None:
    assert parse_fields(b"one\0two", "mx") == [("m", b"one"), ("x", b"two")]
    assert parse_fields(b"\0\0\0\2ab" + b"text", "Wm") == [("W", b"ab"), ("m", b"text")]
    raw = b"mone\0" + b"P\0\0\0\3abc" + b"htwo\0"
    assert parse_fields(raw) == [("m", b"one"), ("P", b"abc"), ("h", b"two")]


def test_prefix_and_lookup(fruit_ifo: Path) -> None:
    d = Dictionary.from_ifo(fruit_ifo)
    d.open()
    assert d.prefix("ap", 10) == ["Apple", "apple", "apples", "apricot"]
    assert d.prefix("AP", 2) == ["Apple", "apple"]
    assert d.prefix("z", 10) == []
    [entry] = d.lookup("apple")
    assert entry.fields == [("m", b"A round fruit.")]
    assert d.lookup("Apple")[0].fields == [("m", b"A technology company.")]
    assert d.lookup("APPLE") == []
    assert sorted(d.find_words("APPLE")) == ["Apple", "apple"]
    d.close()


def test_synonyms(fruit_ifo: Path) -> None:
    d = Dictionary.from_ifo(fruit_ifo)
    d.open()
    assert "pomme" in d.prefix("po", 10)
    [entry] = d.lookup("pomme")
    assert entry.word == "apple"
    assert entry.synonym == "pomme"


@pytest.mark.parametrize("dictzip", [False, True])
@pytest.mark.parametrize("offset_bits", [32, 64])
def test_formats(tmp_path: Path, dictzip: bool, offset_bits: int) -> None:
    entries = {f"word{n:03}": f"definition {n} " * 5 for n in range(100)}
    ifo = write_dictionary(tmp_path, "d", entries, dictzip=dictzip, offset_bits=offset_bits)
    d = Dictionary.from_ifo(ifo)
    d.open()
    for word, definition in entries.items():
        assert d.lookup(word)[0].fields == [("m", definition.encode())]


def test_compressed_idx(tmp_path: Path) -> None:
    ifo = write_dictionary(tmp_path, "d", {"a": "1", "b": "2"})
    idx = tmp_path / "d.idx"
    (tmp_path / "d.idx.gz").write_bytes(gzip.compress(idx.read_bytes()))
    idx.unlink()
    d = Dictionary.from_ifo(ifo)
    d.open()
    assert d.lookup("b")[0].fields == [("m", b"2")]


def test_missing_files(tmp_path: Path) -> None:
    ifo = write_dictionary(tmp_path, "d", {"a": "1"})
    (tmp_path / "d.dict").unlink()
    with pytest.raises(DictionaryError, match="no .dict"):
        Dictionary.from_ifo(ifo)


def test_offset_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(index_module, "CACHE_MIN_SIZE", 0)
    ifo = write_dictionary(tmp_path / "dic", "d", {"a": "1", "b": "2"})
    cache = tmp_path / "cache"
    d = Dictionary.from_ifo(ifo)
    d.open(cache)
    [cache_file] = cache.iterdir()
    assert d.prefix("", 10) == ["a", "b"]
    d.close()

    scans = []
    real_scan = index_module._scan
    monkeypatch.setattr(index_module, "_scan", lambda *a: scans.append(1) or real_scan(*a))
    d = Dictionary.from_ifo(ifo)
    d.open(cache)
    assert scans == []  # served from the cache
    assert d.lookup("b")[0].fields == [("m", b"2")]
    d.close()

    # Rewriting the index invalidates the cache.
    write_dictionary(tmp_path / "dic", "d", {"a": "1", "b": "2", "c": "3"})
    os.utime(tmp_path / "dic" / "d.idx", ns=(1, 1))
    d = Dictionary.from_ifo(ifo)
    d.open(cache)
    assert scans == [1]
    assert d.prefix("", 10) == ["a", "b", "c"]


def test_library_merges_and_dedupes(fruit_ifo: Path, animals_ifo: Path) -> None:
    lib = Library([Dictionary.from_ifo(fruit_ifo), Dictionary.from_ifo(animals_ifo)])
    lib.open()
    assert lib.prefix("a") == ["aardvark", "Apple", "apple", "apples", "apricot"]
    assert lib.prefix("") == []
    results = lib.lookup("apple")
    assert [r.dictionary.name for r in results] == ["Fruit", "Animals"]
    assert lib.lookup("badger")[0].entries[0].fields[0][1].startswith(b"<k>badger")


def test_library_drops_broken_dictionaries(fruit_ifo: Path, tmp_path: Path) -> None:
    ifo = write_dictionary(tmp_path / "broken", "b", {"a": "1"}, dictzip=True)
    (tmp_path / "broken" / "b.dict.dz").write_bytes(b"not gzip")
    lib = Library([Dictionary.from_ifo(fruit_ifo), Dictionary.from_ifo(ifo)])
    lib.open()
    assert [d.name for d in lib.dictionaries] == ["Fruit"]
    assert len(lib.errors) == 1


def test_fuzzy(fruit_ifo: Path, animals_ifo: Path) -> None:
    lib = Library([Dictionary.from_ifo(fruit_ifo), Dictionary.from_ifo(animals_ifo)])
    lib.open()
    assert lib.fuzzy("aplpe")[:2] == ["Apple", "apple"]
    assert lib.fuzzy("bananna") == ["banana"]
    assert lib.fuzzy("qqqqqq") == []


def test_edit_distance() -> None:
    assert edit_distance("apple", "apple") == 0
    assert edit_distance("aplpe", "apple") == 1
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
