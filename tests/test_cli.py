from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from pydict import cli


def test_list_with_dict(fruit_ifo: Path, animals_ifo: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["-l", "-d", str(fruit_ifo), "-d", str(animals_ifo.parent)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Dictionary's name")
    assert [line.split()[0] for line in out[1:]] == ["Fruit", "Animals"]


def test_list_with_dict_and_use_dict(fruit_ifo: Path, animals_ifo: Path, capsys) -> None:
    assert cli.main(["-l", "-d", str(fruit_ifo), "-d", str(animals_ifo), "-u", "Animals"]) == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:]] == ["Animals"]


def test_list_with_data_dir(fruit_ifo: Path, animals_ifo: Path, tmp_path: Path, capsys) -> None:
    assert cli.main(["-l", "-x", "-2", str(tmp_path / "dic")]) == 0
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:]] == ["Animals", "Fruit"]


def test_use_dict_matches_part_of_name(fruit_ifo: Path, animals_ifo: Path, capsys) -> None:
    assert cli.main(["-l", "-d", str(fruit_ifo), "-d", str(animals_ifo), "-u", "ANIM", "-u", "zzz"]) == 0
    captured = capsys.readouterr()
    assert [line.split()[0] for line in captured.out.splitlines()[1:]] == ["Animals"]
    assert "No dictionary matches: zzz" in captured.err


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        ([], {}, False),
        ([], {"NO_COLOR": ""}, False),  # empty means unset
        ([], {"NO_COLOR": "1"}, True),
        ([], {"NO_COLOR": "0"}, True),  # any non-empty value
        (["--no-color"], {}, True),
    ],
)
def test_no_color_requested(argv, env, expected) -> None:
    args = cli.build_parser().parse_args(argv)
    assert cli.no_color_requested(args, env) is expected


def test_bad_dict_path(tmp_path: Path, capsys) -> None:
    assert cli.main(["-l", "-d", str(tmp_path / "nope.ifo")]) == 1
    assert "nope" in capsys.readouterr().err


def test_only_data_dir_needs_data_dir() -> None:
    with pytest.raises(SystemExit):
        cli.main(["-x"])


def test_background_option() -> None:
    from textual.color import Color

    parser = cli.build_parser()
    assert parser.parse_args(["--background", "light"]).background == Color(255, 255, 255)
    assert parser.parse_args(["--background", "#002b36"]).background == Color(0, 43, 54)
    with pytest.raises(SystemExit):
        parser.parse_args(["--background", "nope"])


def test_focus_color_option() -> None:
    from textual.color import Color

    parser = cli.build_parser()
    assert parser.parse_args([]).focus_color is None
    assert parser.parse_args(["--focus-color", "#d33682"]).focus_color == Color(211, 54, 130)
    assert parser.parse_args(["--focus-color", "red"]).focus_color == Color(255, 0, 0)
    with pytest.raises(SystemExit):
        parser.parse_args(["--focus-color", "nope"])


def test_serve_command(fruit_ifo: Path) -> None:
    args = cli.build_parser().parse_args(
        ["--serve", "-d", str(fruit_ifo), "-u", "Fruit Book", "--no-cache", "--no-color",
         "--focus", "definition", "--focus-color", "red", "--", "-word"]
    )
    argv = shlex.split(cli.serve_command(args))
    assert argv[:3] == [sys.executable, "-m", "pydict"]
    assert argv[3:] == ["--dict", str(fruit_ifo.resolve()), "--use-dict", "Fruit Book",
                        "--no-cache", "--no-color", "--focus", "definition",
                        "--focus-color", "#FF0000", "--", "-word"]
    assert cli.build_parser().parse_args(argv[3:]).word == "-word"


def test_focus_option() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["word"]).focus is None
    assert parser.parse_args(["--focus", "wordlist", "word"]).focus == "wordlist"
    assert set(cli.FOCUS_CHOICES) == {"wordlist", "definition"}
    with pytest.raises(SystemExit):
        parser.parse_args(["--focus", "lookup"])
