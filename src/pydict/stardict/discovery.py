"""Find dictionaries the way ``sdcv`` (0.5.x) does.

Directories, in order:

1. the user directories: ``~/.stardict/dic`` and
   ``$XDG_DATA_HOME/stardict/dic`` (skipped with ``only_data_dir``);
2. the data directory: ``data_dir`` if given, otherwise ``$STARDICT_DATA_DIR``
   or ``/usr/share/stardict/dic`` (none with ``only_data_dir`` and no
   ``data_dir``).

Each directory is searched recursively for ``*.ifo`` files. Dictionaries named
in the ordering file (``$XDG_CONFIG_HOME/sdcv_ordering`` or
``~/.sdcv_ordering``, one bookname per line) come first; with ``use_dicts``
only the matching dictionaries are used, in the given order.

Unlike sdcv, both user directories are searched (sdcv skips the XDG one
whenever ``~/.stardict`` exists, even if it is empty), directory entries are
visited in sorted order rather than filesystem order, so results are
reproducible, and ``use_dicts`` matches any part of the bookname, ignoring
case.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydict.stardict.dictionary import DictInfo, DictionaryError, parse_ifo


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def _xdg(env: Mapping[str, str], var: str, default: str) -> Path:
    value = env.get(var)
    return Path(value) if value and os.path.isabs(value) else _home(env) / default


def user_dirs(env: Mapping[str, str] = os.environ) -> list[Path]:
    return [
        _home(env) / ".stardict" / "dic",
        _xdg(env, "XDG_DATA_HOME", ".local/share") / "stardict" / "dic",
    ]


def search_dirs(
    data_dir: str | Path | None = None,
    only_data_dir: bool = False,
    env: Mapping[str, str] = os.environ,
) -> list[Path]:
    dirs: list[Path] = []
    if not only_data_dir:
        dirs += user_dirs(env)
    if data_dir is not None:
        dirs.append(Path(data_dir).expanduser())
    elif not only_data_dir:
        dirs.append(Path(env.get("STARDICT_DATA_DIR") or "/usr/share/stardict/dic"))
    unique: list[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def ordering_file(env: Mapping[str, str] = os.environ) -> Path | None:
    for path in (
        _xdg(env, "XDG_CONFIG_HOME", ".config") / "sdcv_ordering",
        _home(env) / ".sdcv_ordering",
    ):
        if path.is_file():
            return path
    return None


def find_ifo_files(directory: Path) -> list[Path]:
    """All ``*.ifo`` files under ``directory``, depth-first in sorted order."""
    found: list[Path] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        path = Path(entry.path)
        try:
            if entry.is_dir():
                found += find_ifo_files(path)
            elif entry.name.endswith(".ifo") and entry.is_file():
                found.append(path)
        except OSError:
            continue
    return found


def select_dicts(
    infos: list[DictInfo], patterns: Iterable[str]
) -> tuple[list[DictInfo], list[str]]:
    """Dictionaries whose bookname contains any of ``patterns``, ignoring case.

    They are ordered by the first pattern they match, then by their order in
    ``infos``. Also returns a warning for each pattern that matches nothing.
    """
    selected: list[DictInfo] = []
    warnings: list[str] = []
    for pattern in patterns:
        folded = pattern.casefold()
        matches = [info for info in infos if folded in info.bookname.casefold()]
        if not matches:
            warnings.append(f"No dictionary matches: {pattern}")
        selected += [info for info in matches if info not in selected]
    return selected, warnings


@dataclass
class Discovery:
    dictionaries: list[DictInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def discover(
    data_dir: str | Path | None = None,
    only_data_dir: bool = False,
    use_dicts: Iterable[str] | None = None,
    env: Mapping[str, str] = os.environ,
) -> Discovery:
    result = Discovery()
    infos: list[DictInfo] = []
    seen: set[Path] = set()  # the directories may overlap through symlinks
    for directory in search_dirs(data_dir, only_data_dir, env):
        for path in find_ifo_files(directory):
            real = path.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                infos.append(parse_ifo(path))
            except DictionaryError as e:
                result.warnings.append(str(e))

    if use_dicts:
        result.dictionaries, warnings = select_dicts(infos, use_dicts)
        result.warnings += warnings
        return result

    names: list[str] = []
    order = ordering_file(env)
    if order is not None:
        try:
            names = [line.rstrip("\r\n") for line in order.open(encoding="utf-8")]
        except OSError as e:
            result.warnings.append(f"{order}: {e.strerror}")
        names = [n for n in names if n]

    by_name = {info.bookname: info for info in infos}
    ordered: list[DictInfo] = []
    for name in names:
        info = by_name.get(name)
        if info is None:
            result.warnings.append(f"Unknown dictionary: {name}")
        elif info not in ordered:
            ordered.append(info)
    ordered += [info for info in infos if info not in ordered]
    result.dictionaries = ordered
    return result


def resolve_dict_path(path: str | Path) -> list[Path]:
    """``.ifo`` files for a user-supplied dictionary path.

    Accepts the ``.ifo`` itself, any file of the set (``.idx``, ``.dict``,
    ``.dict.dz``, ...), or a directory to search recursively.
    """
    p = Path(path).expanduser()
    if p.is_dir():
        found = find_ifo_files(p)
        if not found:
            raise DictionaryError(f"{p}: no .ifo files found")
        return found
    name = p.name
    for suffix in (".dict.dz", ".idx.gz", ".ifo", ".idx", ".dict", ".syn"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    ifo = p.with_name(name + ".ifo")
    if not ifo.is_file():
        raise DictionaryError(f"{path}: no such dictionary ({ifo.name} not found)")
    return [ifo]
