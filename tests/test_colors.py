from __future__ import annotations

import pytest
from textual.color import Color

from pydict import colors
from pydict.colors import contrast_ratio, readable

BACKGROUNDS = ["#000000", "#ffffff", "#fdf6e3", "#002b36", "#1e1e2e", "#777777"]


def test_contrast_ratio() -> None:
    assert contrast_ratio(colors.DARK, colors.LIGHT) == pytest.approx(21)
    assert contrast_ratio(Color(10, 20, 30), Color(10, 20, 30)) == pytest.approx(1)


@pytest.mark.parametrize("background", BACKGROUNDS)
@pytest.mark.parametrize("color", ["#2f4f4f", "#8b008b", "#808080", "#ffff00", "#000080", "#fdf6e3"])
def test_readable(color: str, background: str) -> None:
    bg = Color.parse(background)
    fg = Color.parse(color)
    result = readable(fg, bg)
    best = max(contrast_ratio(colors.DARK, bg), contrast_ratio(colors.LIGHT, bg))
    assert contrast_ratio(result, bg) >= min(colors.MIN_CONTRAST, best) - 0.01
    if contrast_ratio(fg, bg) >= colors.MIN_CONTRAST:
        assert result == fg  # already readable colors are left alone


def test_readable_changes_as_little_as_needed() -> None:
    result = readable(Color.parse("#2f4f4f"), colors.DARK)
    assert contrast_ratio(result, colors.DARK) < colors.MIN_CONTRAST + 0.1


def test_is_dark() -> None:
    assert colors.is_dark(Color.parse("#002b36"))
    assert not colors.is_dark(Color.parse("#fdf6e3"))


def test_parse_background() -> None:
    assert colors.parse_background("dark") == colors.DARK
    assert colors.parse_background("LIGHT") == colors.LIGHT
    assert colors.parse_background("#fdf6e3") == Color(253, 246, 227)
    with pytest.raises(Exception):
        colors.parse_background("nope")


def test_parse_color_replies() -> None:
    data = (
        b"\x1b]10;rgb:3333/3333/3333\x1b\\"
        b"\x1b]11;rgb:fd/f6/e3\x07"
        b"\x1b[?62;22c"
    )
    assert colors.parse_color_replies(data) == {10: Color(51, 51, 51), 11: Color(253, 246, 227)}
    assert colors.parse_color_replies(b"\x1b[?1;2c") == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("15;0", colors.DARK), ("0;15", colors.LIGHT), ("0;default;7", colors.LIGHT), ("", None)],
)
def test_colorfgbg(value: str, expected: Color | None) -> None:
    assert colors.colorfgbg_background({"COLORFGBG": value}) == expected


@pytest.mark.parametrize("background", BACKGROUNDS)
@pytest.mark.parametrize("foreground", [None, "#333333", "#d0d0d0"])
def test_terminal_theme_is_readable(background: str, foreground: str | None) -> None:
    bg = Color.parse(background)
    fg = Color.parse(foreground) if foreground else None
    theme = colors.terminal_theme(bg, fg)
    best = max(contrast_ratio(colors.DARK, bg), contrast_ratio(colors.LIGHT, bg))
    target = min(colors.MIN_CONTRAST, best) - 0.01
    v = {k: Color.parse(c) for k, c in theme.variables.items()}
    assert theme.dark == colors.is_dark(bg)
    assert contrast_ratio(Color.parse(theme.foreground), bg) >= target
    assert contrast_ratio(v["footer-key-foreground"], bg) >= target
    assert contrast_ratio(v["block-cursor-foreground"], v["block-cursor-background"]) >= colors.MIN_CONTRAST
    assert contrast_ratio(
        v["block-cursor-blurred-foreground"], v["block-cursor-blurred-background"]
    ) >= target
    assert contrast_ratio(v[colors.BORDER_VARIABLE], bg) >= min(colors.MIN_UI_CONTRAST, best) - 0.01


@pytest.mark.parametrize(
    ("value", "expected"),
    [("screen", True), ("screen-256color", True), ("tmux-256color", False), ("xterm-256color", False)],
)
def test_tmux_italics_as_reverse(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    import subprocess

    def run(args, **kwargs):
        assert args[:2] == ["tmux", "show-options"]
        return subprocess.CompletedProcess(args, 0, stdout=value + "\n")

    monkeypatch.setattr(colors.subprocess, "run", run)
    assert colors.tmux_italics_as_reverse({"TMUX": "/tmp/tmux-1000/default,1,0"}) is expected
    assert colors.tmux_italics_as_reverse({}) is False
