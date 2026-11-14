"""Color contrast helpers and terminal color detection."""

from __future__ import annotations

import os
import re
import select
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import replace

from textual.color import Color
from textual.theme import BUILTIN_THEMES, Theme

# WCAG AA contrast for normal text, and for UI parts such as borders.
MIN_CONTRAST = 4.5
MIN_UI_CONTRAST = 3.0

DARK = Color(0, 0, 0)
LIGHT = Color(255, 255, 255)


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: Color) -> float:
    """WCAG relative luminance."""
    return 0.2126 * _channel(color.r) + 0.7152 * _channel(color.g) + 0.0722 * _channel(color.b)


def contrast_ratio(a: Color, b: Color) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_dark(color: Color) -> bool:
    return contrast_ratio(color, LIGHT) > contrast_ratio(color, DARK)


def readable(color: Color, background: Color, min_ratio: float = MIN_CONTRAST) -> Color:
    """``color``, lightened or darkened just enough to be readable on ``background``."""
    if contrast_ratio(color, background) >= min_ratio:
        return color
    target = LIGHT if is_dark(background) else DARK
    lo, hi = 0.0, 1.0  # blend factor towards target; hi always meets the ratio
    if contrast_ratio(target, background) < min_ratio:
        return target  # mid-grey backgrounds can't reach the ratio; do our best
    for _ in range(12):
        mid = (lo + hi) / 2
        if contrast_ratio(color.blend(target, mid), background) >= min_ratio:
            hi = mid
        else:
            lo = mid
    return color.blend(target, hi)


def parse_background(value: str) -> Color:
    """A ``--background`` value: ``dark``, ``light`` or any CSS color."""
    value = value.strip().lower()
    if value == "dark":
        return DARK
    if value == "light":
        return LIGHT
    return Color.parse(value)


# -- terminal detection -------------------------------------------------------

_OSC_REPLY = re.compile(
    rb"\x1b\](1[01]);rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})"
)
_DA1_REPLY = re.compile(rb"\x1b\[\?[0-9;]*c")


def _scale(hex_digits: bytes) -> int:
    return round(int(hex_digits, 16) * 255 / (16 ** len(hex_digits) - 1))


def parse_color_replies(data: bytes) -> dict[int, Color]:
    """OSC 10/11 replies (``ESC ] 11 ; rgb:RRRR/GGGG/BBBB``) in ``data``."""
    return {
        int(m.group(1)): Color(_scale(m.group(2)), _scale(m.group(3)), _scale(m.group(4)))
        for m in _OSC_REPLY.finditer(data)
    }


def query_terminal_colors(timeout: float = 0.3) -> dict[int, Color]:
    """Ask the terminal for its foreground (10) and background (11) colors.

    A DA1 query is sent last; every terminal answers it, so its reply marks
    the end of the answers and we don't wait for the timeout.
    """
    if sys.platform == "win32":
        return {}
    import termios
    import tty

    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return {}
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return {}
    data = b""
    try:
        tty.setraw(fd)
        os.write(fd, b"\x1b]10;?\x1b\\\x1b]11;?\x1b\\\x1b[c")
        deadline = time.monotonic() + timeout
        while not _DA1_REPLY.search(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                break
            chunk = os.read(fd, 1024)
            if not chunk:
                break
            data += chunk
    except OSError:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, saved)
        os.close(fd)
    return parse_color_replies(data)


def colorfgbg_background(env: Mapping[str, str] = os.environ) -> Color | None:
    """Background guessed from ``$COLORFGBG`` (``"fg;bg"``, set by some terminals)."""
    value = env.get("COLORFGBG", "")
    try:
        index = int(value.split(";")[-1])
    except ValueError:
        return None
    return LIGHT if index in (7, 9, 10, 11, 12, 13, 14, 15) else DARK


def tmux_italics_as_reverse(env: Mapping[str, str] = os.environ) -> bool:
    """Whether tmux will draw italics as reverse video.

    tmux uses the standout attribute for italics when its ``default-terminal``
    is ``screen`` or ``screen-*``, since screen's terminfo has no italics.
    """
    if not env.get("TMUX"):
        return False
    try:
        value = subprocess.run(
            ["tmux", "show-options", "-gv", "default-terminal"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return value == "screen" or value.startswith("screen-")


def detect_terminal_colors(
    env: Mapping[str, str] = os.environ,
) -> tuple[Color | None, Color | None]:
    """``(foreground, background)`` of the terminal, ``None`` where unknown."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None, colorfgbg_background(env)
    replies = query_terminal_colors()
    return replies.get(10), replies.get(11) or colorfgbg_background(env)


# -- themes --------------------------------------------------------------------

THEME_NAME = "pydict-terminal"
BORDER_VARIABLE = "pydict-border"


def border_color(background: Color, foreground: Color) -> Color:
    """A subdued color for unfocused borders."""
    return readable(background.blend(foreground, 0.3), background, MIN_UI_CONTRAST)


def terminal_theme(background: Color, foreground: Color | None = None) -> Theme:
    """A Textual theme whose text and UI colors suit the terminal's background."""
    dark = is_dark(background)
    base = BUILTIN_THEMES["textual-dark" if dark else "textual-light"]
    if foreground is None or contrast_ratio(foreground, background) < MIN_CONTRAST:
        foreground = readable(Color.parse("#e0e0e0" if dark else "#1e1e1e"), background)

    def ui(color: str | None) -> str | None:
        if color is None:
            return None
        return readable(Color.parse(color), background, MIN_UI_CONTRAST).hex

    # Textual blends translucent highlight colors over the background, but in
    # a terminal it can't know the real background, so make them opaque here.
    primary = Color.parse(base.primary)
    cursor_text = readable(LIGHT if is_dark(primary) else DARK, primary)
    blurred = background.blend(primary, 0.3)
    footer_key = base.variables.get("footer-key-foreground") or base.accent or base.primary

    return replace(
        base,
        name=THEME_NAME,
        background=background.hex,
        foreground=foreground.hex,
        surface=background.hex,
        panel=background.blend(foreground, 0.1).hex,
        accent=ui(base.accent),
        warning=ui(base.warning),
        error=ui(base.error),
        success=ui(base.success),
        variables={
            **base.variables,
            BORDER_VARIABLE: border_color(background, foreground).hex,
            "block-cursor-foreground": cursor_text.hex,
            "block-cursor-background": primary.hex,
            "block-cursor-blurred-foreground": readable(foreground, blurred).hex,
            "block-cursor-blurred-background": blurred.hex,
            "block-hover-background": background.blend(foreground, 0.1).hex,
            "footer-key-foreground": readable(Color.parse(footer_key), background).hex,
        },
    )
