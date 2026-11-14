"""A small, dependency-free StarDict reader."""

from pydict.stardict.dictionary import (
    DictInfo,
    Dictionary,
    DictionaryError,
    Entry,
    parse_fields,
    parse_ifo,
)
from pydict.stardict.discovery import discover, resolve_dict_path, search_dirs
from pydict.stardict.library import Library, Result

__all__ = [
    "DictInfo",
    "Dictionary",
    "DictionaryError",
    "Entry",
    "Library",
    "Result",
    "discover",
    "parse_fields",
    "parse_ifo",
    "resolve_dict_path",
    "search_dirs",
]
