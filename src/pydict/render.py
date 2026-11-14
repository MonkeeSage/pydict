"""Render dictionary entries as Rich text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote

from rich.style import Style, StyleType
from rich.text import Span, Text
from textual.color import Color, ColorParseError

from pydict.colors import DARK, LIGHT, is_dark, readable
from pydict.stardict import Entry, Result

LINK = Style(color="cyan", underline=True)

# Style meta key holding the word a cross reference points to.
LINK_TARGET = "pydict.link"


def link_style(target: str) -> Style:
    """The style of a cross reference to ``target``; clicking it looks it up."""
    return LINK + Style(meta={"@click": ("app.follow_link", (target,)), LINK_TARGET: target})


@dataclass(frozen=True)
class Link:
    """A cross reference in rendered text."""

    start: int
    end: int
    target: str


def find_links(text: Text) -> list[Link]:
    """The cross references in ``text``, in order."""
    links = [
        Link(span.start, span.end, span.style.meta[LINK_TARGET])
        for span in text.spans
        if isinstance(span.style, Style) and LINK_TARGET in span.style.meta
    ]
    return sorted(links, key=lambda link: link.start)

# Styles shared by HTML, XDXF and Pango markup.
_COMMON_STYLES = {
    "b": Style(bold=True),
    "strong": Style(bold=True),
    "big": Style(bold=True),
    "i": Style(italic=True),
    "em": Style(italic=True),
    "cite": Style(italic=True),
    "u": Style(underline=True),
    "ins": Style(underline=True),
    "s": Style(strike=True),
    "strike": Style(strike=True),
    "del": Style(strike=True),
    "small": Style(dim=True),
    "a": LINK,
}

# https://github.com/soshial/xdxf_makedict/blob/master/format_standard/xdxf_description.md
_XDXF_STYLES = {
    "k": Style(bold=True),
    "abr": Style(color="green", italic=True),
    "pos": Style(color="green", italic=True),
    "gr": Style(color="green", italic=True),
    "ex": Style(italic=True, dim=True),
    "co": Style(dim=True),
    "kref": LINK,
    "iref": LINK,
    "dtrn": Style(),
    "tr": Style(bold=True),
}

_HTML_BLOCKS = {
    "p", "div", "li", "ul", "ol", "dl", "dt", "dd", "tr", "table", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer",
}
_HTML_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "head", "title"}
_VOID = {"br", "hr", "img", "meta", "link", "input", "wbr", "source", "rref"}

_STYLE_COLOR = re.compile(r"(?:^|;)\s*color\s*:\s*([^;]+)", re.I)


def _color(value: str | None, background: Color | None) -> Style | None:
    """A text color from markup, adjusted to be readable on ``background``."""
    if not value:
        return None
    try:
        color = Color.parse(value.strip())
    except (ColorParseError, ValueError):
        return None
    if background is not None:
        color = readable(color, background)
    return Style(color=color.hex)


class _MarkupRenderer(HTMLParser):
    def __init__(self, kind: str, background: Color | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.kind = kind  # "h" (HTML), "x" (XDXF) or "g" (Pango)
        self.background = background
        self.text = Text()
        # Open tags: (tag, style, link), where link is (start offset, target)
        # for cross references; a target of None means the link's text.
        self.stack: list[tuple[str, Style | None, tuple[int, str | None] | None]] = []
        self.quote_depth = 0
        self.skip = 0
        self.lists: list[int | None] = []  # item counter per <ol>, None for <ul>
        self.bol = True  # at the beginning of a line
        self.space = False  # output ends with a space

    def _append(self, s: str, style: Style | str | None = None) -> None:
        if s:
            self.text.append(s, style)
            self.bol = s.endswith("\n")
            self.space = s.endswith(" ")

    # -- output helpers ----------------------------------------------------

    def _style(self) -> Style:
        style = Style()
        for _, s, _ in self.stack:
            if s is not None:
                style += s
        return style

    def _write(self, s: str) -> None:
        if not s:
            return
        indent = "  " if self.quote_depth else ""
        style = self._style()
        lines = s.split("\n")
        for n, line in enumerate(lines):
            if n:
                self._append("\n")
            if line and indent and self.bol:
                self._append(indent)
            self._append(line, style)

    def _newline(self) -> None:
        if not self.bol:
            self._append("\n")

    # -- parser callbacks --------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in _SKIP:
            self.skip += 1
            return
        if tag in _VOID:
            self.handle_startendtag(tag, attrs)
            return
        html = self.kind == "h"
        style: Style | None = _COMMON_STYLES.get(tag)
        if self.kind == "x":
            style = _XDXF_STYLES.get(tag, style)
            if tag == "c":
                style = _color(a.get("c"), self.background) or Style(color="green")
            if tag == "tr":
                self._write("[")
        if tag == "font":
            style = _color(a.get("color"), self.background)
        elif tag == "span":
            value = a.get("foreground") or a.get("fgcolor") or a.get("color")
            style = _color(value, self.background)
            match = _STYLE_COLOR.search(a.get("style") or "")
            if match:
                style = _color(match.group(1), self.background) or style
        elif html and tag in _HTML_HEADINGS:
            style = Style(bold=True)
        elif html and "style" in a:
            match = _STYLE_COLOR.search(a.get("style") or "")
            if match:
                style = _color(match.group(1), self.background)

        if tag == "blockquote":
            self._newline()
            self.quote_depth += 1
        elif html and tag in _HTML_BLOCKS:
            self._newline()
            if tag == "ul":
                self.lists.append(None)
            elif tag == "ol":
                self.lists.append(0)
            elif tag == "li":
                counter = self.lists[-1] if self.lists else None
                if counter is None:
                    self._write("• ")
                else:
                    self.lists[-1] = counter + 1
                    self._write(f"{counter + 1}. ")
        elif self.kind == "x" and tag == "def":
            self._newline()
        link: tuple[int, str | None] | None = None
        href = a.get("href") or ""
        if tag == "a" and href.startswith("bword://"):
            link = (len(self.text), unquote(href[len("bword://"):]))
        elif self.kind == "x" and tag == "kref":
            link = (len(self.text), None)
        self.stack.append((tag, style, link))

    def _end_link(self, start: int, target: str | None) -> None:
        plain = self.text.plain
        end = len(plain)
        # Leave out surrounding whitespace and line breaks.
        while start < end and plain[start].isspace():
            start += 1
        while end > start and plain[end - 1].isspace():
            end -= 1
        target = (plain[start:end] if target is None else target).strip()
        if target and end > start:
            self.text.stylize(link_style(target), start, end)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if not any(t == tag for t, _, _ in self.stack):
            return  # stray close tag
        while self.stack:
            open_tag, _, link = self.stack.pop()
            if link is not None:
                self._end_link(*link)
            if open_tag == tag:
                break
        if self.kind == "x" and tag == "tr":
            self._write("]")
        if tag == "blockquote":
            self.quote_depth = max(0, self.quote_depth - 1)
            self._newline()
        elif self.kind == "h" and tag in _HTML_BLOCKS:
            if tag in ("ul", "ol") and self.lists:
                self.lists.pop()
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._append("\n")
        elif tag == "hr":
            self._newline()
            self._append("─" * 20 + "\n", Style(dim=True))
        elif tag == "img":
            alt = dict(attrs).get("alt")
            self._write(f"[image: {alt}]" if alt else "[image]")
        elif tag == "rref":
            self._write("[resource]")
        elif tag not in _VOID:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.kind == "h":
            data = re.sub(r"\s+", " ", data)
            if self.bol or self.space:
                data = data.lstrip()
        else:
            data = data.replace("\t", "  ")
        self._write(data)


def render_markup(markup: str, kind: str, background: Color | None = None) -> Text:
    renderer = _MarkupRenderer(kind, background)
    renderer.feed(markup)
    renderer.close()
    for _, _, link in reversed(renderer.stack):  # unclosed links
        if link is not None:
            renderer._end_link(*link)
    text = renderer.text
    text.rstrip()
    return text


_BINARY_LABELS = {"W": "[audio]", "P": "[image]"}


def render_field(kind: str, data: bytes, background: Color | None = None) -> Text:
    if kind.isupper():
        return Text(_BINARY_LABELS.get(kind, "[binary data]"), style="dim")
    s = data.decode("utf-8", "replace")
    if kind in ("h", "x", "g", "k"):
        return render_markup(s, "x" if kind == "k" else kind, background)
    if kind == "t":
        return Text(f"[{s}]", style="bold")
    if kind == "r":
        return Text("[resources: " + ", ".join(s.split()) + "]", style="dim")
    return Text(s.rstrip())


def render_entry(entry: Entry, background: Color | None = None) -> Text:
    body = Text("\n").join(render_field(kind, data, background) for kind, data in entry.fields)
    text = Text()
    # Many dictionaries already start the entry with the headword.
    if not body.plain.lstrip().startswith(entry.word):
        text.append(entry.word, style="bold")
    if entry.synonym is not None and entry.synonym != entry.word:
        text.append(f"  (from {entry.synonym})", style="dim italic")
    if text.plain:
        text.append("\n")
    text.append_text(body)
    return text


@dataclass(frozen=True)
class Palette:
    """The colors text is shown in, used to replace attributes whose look is
    left to the terminal (``dim``, ``reverse``) with explicit, readable colors.
    """

    background: Color
    foreground: Color
    highlight: Color  # background of reverse-video text such as headers
    italics: bool = True  # False where the terminal can't show italics properly
    color: bool = True  # False to strip colors, keeping reverse and dim

    @property
    def highlight_text(self) -> Color:
        return readable(LIGHT if is_dark(self.highlight) else DARK, self.highlight)

    def muted(self, color: Color | None = None) -> Color:
        base = self.foreground if color is None else color
        return readable(base.blend(self.background, 0.35), self.background)

    def resolve(self, style: StyleType) -> Style:
        style = Style.parse(style) if isinstance(style, str) else style
        if not self.color:
            meta = style.meta  # without_color drops it
            style = style.without_color + Style(meta=meta) if meta else style.without_color
            return style + Style(italic=False) if style.italic and not self.italics else style
        if style.reverse:
            style += Style(
                reverse=False, color=self.highlight_text.hex, bgcolor=self.highlight.hex
            )
        if style.italic and not self.italics:
            style += Style(italic=False)
        if style.dim:
            triplet = style.color.triplet if style.color else None
            if triplet is not None or style.color is None:
                base = Color(*triplet) if triplet else None
                style += Style(dim=False, color=self.muted(base).hex)
            else:
                style += Style(dim=False)  # a terminal palette color; keep it bright
        return style

    def apply(self, text: Text) -> Text:
        """``text`` with every style resolved against this palette."""
        out = text.copy()
        out.style = self.resolve(text.style) if text.style else text.style
        out.spans = [Span(s.start, s.end, self.resolve(s.style)) for s in text.spans]
        return out


def render_results(results: list[Result], palette: Palette | None = None) -> Text:
    """Results as text, with colors made readable against ``palette``."""
    background = palette.background if palette else None
    text = Text()
    for n, result in enumerate(results):
        if n:
            text.append("\n\n")
        text.append(f" {result.dictionary.name} ", style="bold reverse")
        for entry in result.entries:
            text.append("\n\n")
            text.append_text(render_entry(entry, background))
    return palette.apply(text) if palette else text
