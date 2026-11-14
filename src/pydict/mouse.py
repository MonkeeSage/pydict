"""Report the mouse's back button, which Textual mistakes for a left click.

xterm reports buttons 8 to 11 (back, forward, ...) with bit 128 set in the
button code, which Textual's parser ignores, so the back button arrives as a
press of the left button and the forward button as the middle one. This
patches the parser to turn a back button press into a ``MouseBack`` event
(posted to the app) and to drop every other report of those buttons.
"""

from __future__ import annotations

from textual import events
from textual._xterm_parser import XTermParser
from textual.message import Message

_EXTRA_BUTTONS = 128  # buttons 8-11
_MODIFIERS = 4 | 8 | 16  # shift, meta, ctrl
_MOTION = 32


class MouseBack(events.Event):
    """The mouse's back button was pressed."""


def install() -> None:
    """Patch Textual's input parser (once)."""
    original = XTermParser.parse_mouse_code
    if getattr(original, "_pydict", False):
        return

    def parse_mouse_code(self: XTermParser, code: str) -> Message | None:
        match = self._re_sgr_mouse.match(code)
        if match is not None:
            buttons = int(match.group(1))
            if buttons & _EXTRA_BUTTONS:
                if buttons & ~_MODIFIERS == _EXTRA_BUTTONS and match.group(4) == "M":
                    return MouseBack()
                return None
        return original(self, code)

    parse_mouse_code._pydict = True  # type: ignore[attr-defined]
    XTermParser.parse_mouse_code = parse_mouse_code  # type: ignore[method-assign]
