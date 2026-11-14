from __future__ import annotations

import subprocess

import pytest

from pydict import clipboard


@pytest.mark.parametrize(
    "env",
    [
        {"TERM": "xterm-kitty"},
        {"KITTY_WINDOW_ID": "1", "TERM": "xterm-256color"},
        {"TERM_PROGRAM": "WezTerm"},
        {"TERM": "foot"},
        {"TERM": "alacritty"},
        {"TERM_PROGRAM": "ghostty"},
        {"TERM_PROGRAM": "iTerm.app"},
        {"LC_TERMINAL": "iTerm2"},
        {"WT_SESSION": "abc"},
    ],
)
def test_osc52_known_terminals(env: dict[str, str]) -> None:
    assert clipboard.terminal_supports_osc52(env)


@pytest.mark.parametrize(
    "env",
    [
        {"TERM": "xterm-256color"},
        {"TERM_PROGRAM": "Apple_Terminal"},
        {"TERM": "screen-256color", "STY": "1.pts"},
        {},
    ],
)
def test_osc52_unknown_terminals(env: dict[str, str]) -> None:
    assert not clipboard.terminal_supports_osc52(env)


def test_osc52_tmux_delegates() -> None:
    env = {"TMUX": "/tmp/tmux", "TERM": "xterm-kitty"}
    assert clipboard.terminal_supports_osc52(env, tmux_check=lambda: True)
    assert not clipboard.terminal_supports_osc52(env, tmux_check=lambda: False)


def test_tmux_check(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {}
    monkeypatch.setattr(clipboard, "_run", lambda args, timeout=2.0: answers[args[1]])
    answers.update({"show-options": "external",
                    "display-message": "focus,clipboard,RGB\txterm-kitty\tkitty(0.36.4)"})
    assert clipboard.tmux_supports_osc52()
    answers.update({"show-options": "off"})
    assert not clipboard.tmux_supports_osc52()
    answers.update({"show-options": "on", "display-message": "focus,RGB\txterm-kitty\tkitty(0.36.4)"})
    assert not clipboard.tmux_supports_osc52()


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ("clipboard\txterm-256color\tWezTerm 20240203", True),
        ("clipboard\tfoot\t", True),  # tmux too old to report client_termtype
        ("clipboard,RGB\txterm-256color\tKonsole 26.08.2", False),
        ("clipboard\txterm-256color\tXTerm(390)", False),
        ("clipboard\txterm-256color\t", False),
        ("", False),
    ],
)
def test_tmux_check_outer_terminal(
    monkeypatch: pytest.MonkeyPatch, info: str, expected: bool
) -> None:
    answers = {"show-options": "external", "display-message": info}
    monkeypatch.setattr(clipboard, "_run", lambda args, timeout=2.0: answers[args[1]])
    assert clipboard.tmux_supports_osc52() is expected


def test_system_commands_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "_is_wsl", lambda: False)
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert [c[0] for c in clipboard.system_commands(env)] == ["wl-copy", "xclip", "xsel"]
    assert clipboard.system_commands({}) == []


def test_system_commands_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    assert clipboard.system_commands({}) == [["pbcopy"]]
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    assert clipboard.system_commands({}) == [["clip"]]
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard, "_is_wsl", lambda: True)
    assert clipboard.system_commands({})[0] == ["clip.exe"]


def test_system_copy_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "system_commands", lambda env: [["missing"], ["broken"], ["good", "-x"]])
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None if name == "missing" else f"/bin/{name}")
    calls = []

    def run(args, input, **kwargs):
        calls.append((args, input))
        if args[0] == "/bin/broken":
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.system_copy("héllo") == "good"
    assert calls[-1] == (["/bin/good", "-x"], "héllo".encode())


def test_system_copy_nothing_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "system_commands", lambda env: [])
    with pytest.raises(clipboard.ClipboardError):
        clipboard.system_copy("x")


def test_resolve_mode() -> None:
    assert clipboard.resolve_mode(None, {}) == "auto"
    assert clipboard.resolve_mode(None, {clipboard.ENV_VAR: "OSC52"}) == "osc52"
    assert clipboard.resolve_mode("system", {clipboard.ENV_VAR: "osc52"}) == "system"
    with pytest.raises(ValueError):
        clipboard.resolve_mode(None, {clipboard.ENV_VAR: "bogus"})
