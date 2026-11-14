"""Searching across several dictionaries."""

from __future__ import annotations

import os
import string
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydict.stardict.dictionary import Dictionary, DictionaryError, Entry
from pydict.stardict.index import sort_key


def word_key(word: str) -> tuple[bytes, bytes]:
    return sort_key(word.encode("utf-8"))


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, counting adjacent transpositions as one edit."""
    before: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            cost = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            if before is not None and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, before[j - 2] + 1)
            current.append(cost)
        before, previous = previous, current
    return previous[-1]


def edits1(word: str) -> set[str]:
    """Strings one deletion, transposition, replacement or insertion away."""
    letters = set(string.ascii_lowercase) | set(word) | {"-", "'", " "}
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    out |= {a + c + b[1:] for a, b in splits if b for c in letters}
    out |= {a + c + b for a, b in splits for c in letters}
    out.discard(word)
    out.discard("")
    return out


@dataclass
class Result:
    dictionary: Dictionary
    entries: list[Entry]


class Library:
    def __init__(self, dictionaries: Iterable[Dictionary] = ()) -> None:
        self.dictionaries = list(dictionaries)
        self.errors: list[str] = []

    def open(self, cache_dir: Path | None = None) -> None:
        """Open every dictionary, dropping (and recording) ones that fail."""
        opened = []
        for d in self.dictionaries:
            try:
                d.open(cache_dir)
                opened.append(d)
            except (OSError, ValueError, zlib.error) as e:
                self.errors.append(f"{d.info.ifo_path}: {e}")
        self.dictionaries = opened

    def close(self) -> None:
        for d in self.dictionaries:
            d.close()

    def prefix(self, prefix: str, limit: int = 200) -> list[str]:
        """Distinct words starting with ``prefix``, in StarDict order."""
        if not prefix:
            return []
        words = {w for d in self.dictionaries for w in d.prefix(prefix, limit)}
        return sorted(words, key=word_key)[:limit]

    def fuzzy(self, query: str, limit: int = 50) -> list[str]:
        """Words similar to ``query``, closest first.

        Candidates are words one edit away (any position) plus words sharing
        the longest prefix with ``query`` that matches anything; they are
        ranked by edit distance.
        """
        if not query:
            return []
        folded = query.lower()
        max_distance = 1 if len(query) <= 4 else 2 if len(query) <= 8 else 3
        candidates: set[str] = set()
        for variant in edits1(folded):
            for d in self.dictionaries:
                candidates.update(d.find_words(variant))
        for k in range(len(query) - 1, 0, -1):
            near = self.prefix(query[:k], 2000)
            if near:
                candidates.update(near)
                break
        letters = sorted(folded)
        scored = []
        for word in candidates:
            lower = word.lower()
            distance = edit_distance(folded, lower)
            if distance <= max_distance:
                # Ties: prefer the same letters (likely a transposition typo),
                # then the same length, then a longer shared prefix.
                shared = len(os.path.commonprefix([folded, lower]))
                scored.append((
                    distance, sorted(lower) != letters, abs(len(lower) - len(folded)),
                    -shared, word_key(word), word,
                ))
        scored.sort()
        return [s[-1] for s in scored[:limit]]

    def lookup(self, word: str) -> list[Result]:
        results = []
        for d in self.dictionaries:
            try:
                entries = d.lookup(word)
            except DictionaryError as e:
                self.errors.append(str(e))
                continue
            if entries:
                results.append(Result(d, entries))
        return results
