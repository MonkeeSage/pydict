"""Copying text to the clipboard.

Terminals can't reliably be asked whether they accept OSC 52 clipboard
writes, so support is guessed from the environment. When OSC 52 looks
unsupported, a platform clipboard tool is used instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from functools import lru_cache

MODES = ("auto", "osc52", "system")
ENV_VAR = "PYDICT_CLIPBOARD"


class ClipboardError(RuntimeError):
    pass


def _run(args: list[str], timeout: float = 2.0) -> str | None:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


# Outer terminals known to accept OSC 52, matched against tmux's
# ``client_termtype`` (the terminal's XTVERSION reply, e.g. "kitty(0.36.4)").
_OSC52_TERMTYPES = ("kitty", "wezterm", "foot", "ghostty", "iterm2", "alacritty")


def _known_osc52_term(term: str) -> bool:
    return term in ("xterm-kitty", "alacritty", "xterm-ghostty") or term.startswith("foot")


def tmux_supports_osc52() -> bool:
    """Whether tmux will forward OSC 52 to a terminal that supports it.

    ``set-clipboard`` must not be ``off``, tmux must think the outer terminal
    has the ``clipboard`` feature, and the outer terminal must be one known to
    accept OSC 52. tmux's feature alone isn't enough: the default
    ``terminal-features`` grants ``clipboard`` to every ``xterm*``, which
    includes terminals like Konsole and XTerm that ignore it.
    """
    setting = _run(["tmux", "show-options", "-gv", "set-clipboard"])
    if setting is None or setting == "off":
        return False
    info = _run(["tmux", "display-message", "-p",
                 "#{client_termfeatures}\t#{client_termname}\t#{client_termtype}"])
    if not info:
        return False
    features, _, rest = info.partition("\t")
    termname, _, termtype = rest.partition("\t")
    if "clipboard" not in features.split(","):
        return False
    return _known_osc52_term(termname) or termtype.lower().startswith(_OSC52_TERMTYPES)


def terminal_supports_osc52(
    env: Mapping[str, str] = os.environ,
    tmux_check: Callable[[], bool] = tmux_supports_osc52,
) -> bool:
    """Best guess at whether the terminal accepts OSC 52 clipboard writes."""
    if env.get("TMUX"):
        return tmux_check()
    term = env.get("TERM", "")
    program = env.get("TERM_PROGRAM", "").lower()
    if term.startswith("screen") or env.get("STY"):
        return False  # GNU screen doesn't pass OSC 52 through by default
    return (
        _known_osc52_term(term)
        or "KITTY_WINDOW_ID" in env
        or "WEZTERM_EXECUTABLE" in env or program == "wezterm"
        or "ALACRITTY_WINDOW_ID" in env
        or "GHOSTTY_RESOURCES_DIR" in env or program == "ghostty"
        or program == "iterm.app" or env.get("LC_TERMINAL") == "iTerm2"
        or "WT_SESSION" in env  # Windows Terminal
    )


@lru_cache(maxsize=1)
def _is_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def system_commands(env: Mapping[str, str] = os.environ) -> list[list[str]]:
    """Candidate clipboard commands for this platform, best first."""
    if sys.platform == "darwin":
        return [["pbcopy"]]
    if sys.platform == "win32":
        return [["clip"]]
    commands = []
    if _is_wsl():
        commands.append(["clip.exe"])
    if env.get("WAYLAND_DISPLAY"):
        commands.append(["wl-copy"])
    if env.get("DISPLAY"):
        commands += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    return commands


def system_copy(text: str, env: Mapping[str, str] = os.environ) -> str:
    """Copy with a platform tool. Returns the tool's name."""
    for command in system_commands(env):
        exe = shutil.which(command[0])
        if exe is None:
            continue
        # clip.exe expects UTF-16 to handle non-ASCII text.
        windows = command[0] in ("clip", "clip.exe")
        data = ("﻿" + text).encode("utf-16-le") if windows else text.encode("utf-8")
        try:
            # wl-copy and xclip fork to serve the selection; don't let the
            # child hold our terminal or pipes open.
            subprocess.run(
                [exe, *command[1:]], input=data, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return command[0]
    raise ClipboardError("no working clipboard tool found")


def resolve_mode(mode: str | None, env: Mapping[str, str] = os.environ) -> str:
    """The requested mode (argument, then ``$PYDICT_CLIPBOARD``), validated."""
    mode = (mode or env.get(ENV_VAR) or "auto").lower()
    if mode not in MODES:
        raise ValueError(f"clipboard mode must be one of {', '.join(MODES)}, not {mode!r}")
    return mode
