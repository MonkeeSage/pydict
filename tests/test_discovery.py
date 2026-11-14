from __future__ import annotations

from pathlib import Path

import pytest

from pydict.stardict import DictionaryError, discover, resolve_dict_path, search_dirs
from stardict_writer import write_dictionary


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def env_for(home: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(home), **extra}


def test_search_dirs_default(home: Path) -> None:
    assert search_dirs(env=env_for(home)) == [
        home / ".stardict/dic",
        home / ".local/share/stardict/dic",
        Path("/usr/share/stardict/dic"),
    ]


def test_search_dirs_xdg_data_home(home: Path, tmp_path: Path) -> None:
    # Unlike sdcv, an existing ~/.stardict doesn't hide $XDG_DATA_HOME.
    (home / ".stardict/dic").mkdir(parents=True)
    env = env_for(home, XDG_DATA_HOME=str(tmp_path / "data"), STARDICT_DATA_DIR="/sd")
    assert search_dirs(env=env) == [
        home / ".stardict/dic", tmp_path / "data/stardict/dic", Path("/sd"),
    ]


def test_search_dirs_data_dir_options(home: Path) -> None:
    env = env_for(home, STARDICT_DATA_DIR="/sd")
    assert search_dirs("/mine", env=env) == [
        home / ".stardict/dic", home / ".local/share/stardict/dic", Path("/mine"),
    ]
    assert search_dirs("/mine", only_data_dir=True, env=env) == [Path("/mine")]


def test_discover_both_user_dirs(home: Path, tmp_path: Path) -> None:
    (home / ".stardict/dic").mkdir(parents=True)  # empty, as in the bug report
    data = tmp_path / "data"
    write_dictionary(data / "stardict/dic/w", "w", {"x": "1"}, bookname="Wiktionary")
    found = discover(env=env_for(home, XDG_DATA_HOME=str(data), STARDICT_DATA_DIR=str(tmp_path / "none")))
    assert [i.bookname for i in found.dictionaries] == ["Wiktionary"]


def test_discover_skips_symlinked_duplicates(home: Path, tmp_path: Path) -> None:
    write_dictionary(home / ".stardict/dic", "u", {"x": "1"}, bookname="User")
    data = tmp_path / "data"
    data.mkdir()
    (data / "stardict").symlink_to(home / ".stardict")
    found = discover(env=env_for(home, XDG_DATA_HOME=str(data), STARDICT_DATA_DIR=str(tmp_path / "none")))
    assert [i.bookname for i in found.dictionaries] == ["User"]


def make_dicts(root: Path) -> None:
    write_dictionary(root / "b", "b", {"x": "1"}, bookname="Bravo")
    write_dictionary(root / "a" / "nested", "a", {"x": "1"}, bookname="Alpha")
    write_dictionary(root, "c", {"x": "1"}, bookname="Charlie")


def test_discover_recursive_sorted(home: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    make_dicts(data)
    found = discover(data, only_data_dir=True, env=env_for(home))
    assert [i.bookname for i in found.dictionaries] == ["Alpha", "Bravo", "Charlie"]


def test_discover_user_dir_first(home: Path, tmp_path: Path) -> None:
    write_dictionary(home / ".stardict" / "dic", "u", {"x": "1"}, bookname="User")
    data = tmp_path / "data"
    make_dicts(data)
    found = discover(data, env=env_for(home))
    assert [i.bookname for i in found.dictionaries] == ["User", "Alpha", "Bravo", "Charlie"]


def test_discover_ordering_file(home: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    make_dicts(data)
    (home / ".sdcv_ordering").write_text("Charlie\nNope\nBravo\n")
    found = discover(data, only_data_dir=True, env=env_for(home))
    assert [i.bookname for i in found.dictionaries] == ["Charlie", "Bravo", "Alpha"]
    assert found.warnings == ["Unknown dictionary: Nope"]

    config = tmp_path / "config"
    config.mkdir()
    (config / "sdcv_ordering").write_text("Bravo\n")
    found = discover(data, only_data_dir=True, env=env_for(home, XDG_CONFIG_HOME=str(config)))
    assert [i.bookname for i in found.dictionaries] == ["Bravo", "Alpha", "Charlie"]


def test_discover_use_dicts(home: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    make_dicts(data)
    found = discover(data, only_data_dir=True, use_dicts=["Charlie", "Alpha", "Zulu"],
                     env=env_for(home))
    assert [i.bookname for i in found.dictionaries] == ["Charlie", "Alpha"]
    assert found.warnings == ["No dictionary matches: Zulu"]


def test_discover_use_dicts_matches_part_of_name(home: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_dictionary(data / "oed", "p1", {"x": "1"}, bookname="Oxford English Dictionary P1")
    write_dictionary(data / "oed", "p2", {"x": "1"}, bookname="Oxford English Dictionary P2")
    write_dictionary(data / "wikt", "w", {"x": "1"}, bookname="wikt-en-ALL")
    found = discover(data, only_data_dir=True, use_dicts=["oxford"], env=env_for(home))
    assert [i.bookname for i in found.dictionaries] == [
        "Oxford English Dictionary P1", "Oxford English Dictionary P2",
    ]
    found = discover(data, only_data_dir=True, use_dicts=["WIKT", "p2", "dictionary"],
                     env=env_for(home))
    assert [i.bookname for i in found.dictionaries] == [
        "wikt-en-ALL", "Oxford English Dictionary P2", "Oxford English Dictionary P1",
    ]
    assert found.warnings == []


def test_discover_skips_bad_ifo(home: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    make_dicts(data)
    (data / "junk.ifo").write_text("junk")
    found = discover(data, only_data_dir=True, env=env_for(home))
    assert len(found.dictionaries) == 3
    assert len(found.warnings) == 1


@pytest.mark.parametrize("name", ["d.ifo", "d.idx", "d.dict", "d"])
def test_resolve_dict_path(tmp_path: Path, name: str) -> None:
    ifo = write_dictionary(tmp_path, "d", {"x": "1"})
    if name == "d":
        assert resolve_dict_path(tmp_path) == [ifo]
    else:
        assert resolve_dict_path(tmp_path / name) == [ifo]


def test_resolve_dict_path_dictzip(tmp_path: Path) -> None:
    ifo = write_dictionary(tmp_path, "d", {"x": "1"}, dictzip=True)
    assert resolve_dict_path(tmp_path / "d.dict.dz") == [ifo]


def test_resolve_dict_path_missing(tmp_path: Path) -> None:
    with pytest.raises(DictionaryError):
        resolve_dict_path(tmp_path / "missing.ifo")
    with pytest.raises(DictionaryError):
        resolve_dict_path(tmp_path)
