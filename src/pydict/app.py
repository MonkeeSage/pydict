"""The pydict Textual application."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from rich._wrap import divide_line
from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.color import Color
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.filter import Monochrome, NoColor
from textual.timer import Timer
from textual.widgets import Footer, Input, OptionList, Static

from pydict import clipboard, colors, mouse
from pydict.colors import readable
from pydict.render import Link, Palette, find_links, render_results
from pydict.stardict import DictInfo, Dictionary, DictionaryError, Library

SEARCH_DELAY = 0.12
WORD_LIMIT = 200


# Page up/down scroll the definition from any pane.
PAGE_BINDINGS = [
    Binding("pagedown", "app.scroll_definition('page_down')", show=False),
    Binding("pageup", "app.scroll_definition('page_up')", show=False),
]


# Back from a followed cross reference, in the panes where backspace isn't
# needed for typing.
BACK_BINDING = Binding("backspace", "app.back", "Back")


@dataclass(frozen=True)
class Place:
    """What was shown before a cross reference was followed."""

    query: str  # the lookup box
    word: str | None  # the highlighted word
    highlighted: int | None  # its index in the word list
    scroll_y: float  # of the definition
    link: int | None  # the selected cross reference


class LookupInput(Input):
    """The lookup box. Up/down move through the word list without leaving it."""

    BINDINGS = [
        Binding("down", "word_list('cursor_down')", show=False),
        Binding("up", "word_list('cursor_up')", show=False),
        *PAGE_BINDINGS,
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._focusing_click = False

    def action_word_list(self, action: str) -> None:
        words = self.app.query_one("#words", OptionList)
        getattr(words, f"action_{action}")()

    # Focusing by tab selects all the text; make a click that focuses the box
    # do the same (Input moves the cursor to the click instead), unless the
    # click selected some text by dragging.

    def focus_on_click(self) -> bool:
        # Called just before a mouse press focuses the widget.
        self._focusing_click = not self.has_focus
        return super().focus_on_click()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._focusing_click and self.select_on_focus and self.selection.is_empty:
            self.select_all()
        self._focusing_click = False


class WordList(OptionList):
    """The word list. Enter moves on to the definition (a click doesn't)."""

    BINDINGS = [
        Binding("enter", "app.focus('definition')", "Definition", show=False),
        BACK_BINDING,
        *PAGE_BINDINGS,
    ]


class DefinitionPane(VerticalScroll):
    """The definition. Left/right select a cross reference, enter looks it up."""

    BINDINGS = [
        Binding("right", "app.select_link(1)", "Links", key_display="←→"),
        Binding("left", "app.select_link(-1)", "Previous link", show=False),
        Binding("enter", "app.follow_selected_link", "Look up"),
        BACK_BINDING,
    ]


class PyDictApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "pydict"

    BINDINGS = [
        Binding("ctrl+c", "copy_definition", "Copy definition", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        # Not a priority binding, so escape still closes the command palette.
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        dictionaries: list[DictInfo],
        word: str = "",
        focus: str | None = None,
        clipboard_mode: str = "auto",
        cache_dir: Path | None = None,
        warnings: list[str] | None = None,
        terminal_background: Color | None = None,
        terminal_foreground: Color | None = None,
        no_color: bool = False,
    ) -> None:
        mouse.install()  # before the driver starts parsing input
        super().__init__()
        # Textual applies $NO_COLOR itself, but also when it's empty, and with
        # a filter that keeps grey backgrounds. Remove all color instead, and
        # only when asked (the :nocolor rules in app.tcss add other cues).
        self._filters = [f for f in self._filters if not isinstance(f, (Monochrome, NoColor))]
        self.no_color = no_color
        if no_color:
            self._filters.append(NoColor())
        self.dict_infos = dictionaries
        self.initial_word = word
        self.result_focus = focus  # id of the widget to focus after a lookup (--focus)
        self.clipboard_mode = clipboard_mode
        self.cache_dir = cache_dir
        self.warnings = warnings or []
        self.terminal_background = terminal_background
        self.terminal_foreground = terminal_foreground
        # The background definitions are drawn on; markup colors are made
        # readable against it.
        self.text_background = colors.DARK
        self.italics = True
        self.library: Library | None = None
        self.definition = ""  # plain text of the displayed definition
        self._text = Text()  # the displayed definition, without the link cursor
        self._links: list[Link] = []  # cross references in it
        self._link: int | None = None  # index of the selected one
        self._link_lines: tuple[int, list[int]] | None = None  # (width, line of each link)
        self._history: list[Place] = []  # places left by following cross references
        self._shown_word: str | None = None
        self._search_timer: Timer | None = None
        self._osc52: bool | None = None

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return {colors.BORDER_VARIABLE: "#45505a"}

    def compose(self) -> ComposeResult:
        yield LookupInput(placeholder="Look up a word…", id="lookup")
        with Horizontal(id="results"):
            yield WordList(id="words")
            with DefinitionPane(id="definition"):
                yield Static(id="definition-text")
        yield Footer()

    def on_mount(self) -> None:
        if self.is_web:
            self.text_background = Color.parse(self.get_css_variables()["background"])
        else:
            # Use the terminal's own background (see the :ansi rules in
            # app.tcss), with a theme whose colors are readable on it.
            self.text_background = self.terminal_background or colors.DARK
            self.register_theme(
                colors.terminal_theme(self.text_background, self.terminal_foreground)
            )
            self.theme = colors.THEME_NAME
            self.ansi_color = True
            self.italics = not colors.tmux_italics_as_reverse()
        words = self.query_one("#words", OptionList)
        words.border_title = "Words"
        pane = self.query_one("#definition", VerticalScroll)
        pane.border_title = "Definition"
        self.watch(pane, "scroll_y", self._definition_scrolled, init=False)
        # Links are styled by render.py, not by Textual.
        self.query_one("#definition-text", Static).auto_links = False
        lookup = self.query_one("#lookup", LookupInput)
        lookup.value = self.initial_word
        lookup.focus()
        for warning in self.warnings:
            self.notify(warning, severity="warning", timeout=10)
        if not self.dict_infos:
            self._show_message("No dictionaries found.\n\nSee pydict --help for where pydict looks.")
            return
        self._show_message(f"Loading {len(self.dict_infos)} dictionaries…")
        self.load_library()

    def on_unmount(self) -> None:
        if self.library is not None:
            self.library.close()

    # -- loading ------------------------------------------------------------

    @work(thread=True, exit_on_error=False)
    def load_library(self) -> None:
        dictionaries, errors = [], []
        for info in self.dict_infos:
            try:
                dictionaries.append(Dictionary(info))
            except DictionaryError as e:
                errors.append(str(e))
        library = Library(dictionaries)
        library.open(self.cache_dir)
        errors += library.errors
        library.errors.clear()
        self.call_from_thread(self._library_loaded, library, errors)

    def _library_loaded(self, library: Library, errors: list[str]) -> None:
        self.library = library
        for error in errors:
            self.notify(error, title="Dictionary error", severity="error", timeout=10)
        count = len(library.dictionaries)
        pane = self.query_one("#definition", VerticalScroll)
        pane.border_subtitle = f"{count} dictionar{'y' if count == 1 else 'ies'}"
        self.search()
        # After looking up a word from the command line, focus the word list
        # or definition if asked to (and something was found).
        words = self.query_one("#words", OptionList)
        if self.result_focus and self.initial_word.strip() and words.option_count:
            self.query_one(f"#{self.result_focus}").focus()

    # -- searching ----------------------------------------------------------

    @on(Input.Changed, "#lookup")
    def _lookup_changed(self) -> None:
        # Typing starts over; looking up a cross reference doesn't send this.
        if self._history:
            self._history.clear()
            self.refresh_bindings()
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(SEARCH_DELAY, self.search)

    @on(Input.Submitted, "#lookup")
    def _lookup_submitted(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
            self.search()
        words = self.query_one("#words", OptionList)
        if words.option_count:
            self.query_one(f"#{self.result_focus or 'words'}").focus()

    def search(self) -> None:
        """Fill the word list from the lookup box."""
        self._search_timer = None
        if self.library is None:
            return
        query = self.query_one("#lookup", LookupInput).value.strip()
        words_list = self.query_one("#words", OptionList)
        words_list.clear_options()
        if not query:
            words_list.border_title = "Words"
            self._show_message("Type a word to look it up.")
            return
        words = self.library.prefix(query, WORD_LIMIT)
        fuzzy = not words
        if fuzzy:
            words = self.library.fuzzy(query)
        words_list.border_title = "Did you mean?" if fuzzy else "Words"
        if not words:
            self._show_message(f"No matches for “{query}”.")
            return
        words_list.add_options(words)
        exact = next((n for n, w in enumerate(words) if w == query), None)
        if exact is None:
            folded = query.lower()
            exact = next((n for n, w in enumerate(words) if w.lower() == folded), 0)
        words_list.highlighted = exact
        self.show_definition(words[exact])

    @on(OptionList.OptionHighlighted, "#words")
    def _word_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.show_definition(str(event.option.prompt))

    def show_definition(self, word: str) -> None:
        if self.library is None or word == self._shown_word:
            return
        self._shown_word = word
        results = self.library.lookup(word)
        while self.library.errors:
            self.notify(self.library.errors.pop(0), title="Dictionary error", severity="error")
        if not results:
            self._show_message(f"No definition for “{word}”.")
            self._shown_word = word
            return
        self._show(render_results(results, self.palette()))

    def palette(self) -> Palette:
        variables = self.get_css_variables()
        return Palette(
            background=self.text_background,
            foreground=readable(Color.parse(variables["foreground"]), self.text_background),
            highlight=Color.parse(variables["block-cursor-background"]),
            italics=self.italics,
            color=not self.no_color,
        )

    def _show_message(self, message: str) -> None:
        self._show(self.palette().apply(Text(message, style="dim")))
        self.definition = ""
        self._shown_word = None

    def _show(self, text: Text) -> None:
        self.definition = text.plain
        self._text = text
        self._links = find_links(text)
        self._link = None
        self._link_lines = None
        self._update_definition()
        self.query_one("#definition", VerticalScroll).scroll_home(animate=False, immediate=True)
        self.refresh_bindings()

    def _update_definition(self) -> None:
        text = self._text
        if self._link is not None:
            link = self._links[self._link]
            text = text.copy()
            text.stylize(self.palette().resolve(Style(bold=True, reverse=True)), link.start, link.end)
        self.query_one("#definition-text", Static).update(text)

    def action_scroll_definition(self, action: str) -> None:
        pane = self.query_one("#definition", VerticalScroll)
        getattr(pane, f"action_{action}")()

    # -- cross references ---------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "select_link":
            return bool(self._links)
        if action == "follow_selected_link":
            return self._link is not None
        if action == "back":
            return bool(self._history)
        return True

    def _lines_of_links(self) -> list[int]:
        """The line each link starts on, as wrapped in the definition pane."""
        width = self.query_one("#definition-text", Static).content_size.width
        if self._link_lines is not None and self._link_lines[0] == width:
            return self._link_lines[1]
        # Wrap the way Textual wraps the Static's content.
        lines: list[int] = []
        y = start = 0
        links = iter(self._links)
        link = next(links, None)
        for line in self.definition.split("\n"):
            end = start + len(line)
            breaks = divide_line(line, max(width, 1), fold=True)
            while link is not None and link.start <= end:
                lines.append(y + bisect_right(breaks, link.start - start))
                link = next(links, None)
            y += len(breaks) + 1
            start = end + 1
        self._link_lines = (width, lines)
        return lines

    def _visible_lines(self) -> range:
        pane = self.query_one("#definition", VerticalScroll)
        top = round(pane.scroll_y)
        return range(top, top + pane.scrollable_content_region.height)

    def action_select_link(self, step: int) -> None:
        """Select the next (``step`` 1) or previous (-1) cross reference.

        If the selected one is out of view, start from the visible ones.
        """
        if not self._links:
            return
        lines = self._lines_of_links()
        visible = self._visible_lines()
        if self._link is not None and lines[self._link] in visible:
            index = self._link + step
            if not 0 <= index < len(self._links):
                return
        elif step > 0:
            index = next((n for n, y in enumerate(lines) if y >= visible.start), None)
        else:
            index = next((n for n, y in reversed(list(enumerate(lines))) if y < visible.stop), None)
        if index is None:
            return
        self._link = index
        self._update_definition()
        self.refresh_bindings()
        y = lines[index]
        if y not in visible:
            pane = self.query_one("#definition", VerticalScroll)
            top = y if y < visible.start else y - len(visible) + 1
            pane.scroll_to(y=top, animate=False, immediate=True)

    def _definition_scrolled(self) -> None:
        # Forget a selected link once it's scrolled out of view, so enter
        # doesn't follow a link that can't be seen.
        if self._link is not None and self._lines_of_links()[self._link] not in self._visible_lines():
            self._link = None
            self._update_definition()
            self.refresh_bindings()

    def action_follow_selected_link(self) -> None:
        if self._link is not None:
            self.action_follow_link(self._links[self._link].target)

    def action_follow_link(self, word: str) -> None:
        """Look up ``word`` (a cross reference was clicked or followed)."""
        if self.library is None or word == self._shown_word:
            return
        words = self.query_one("#words", OptionList)
        highlighted = words.highlighted
        self._history.append(Place(
            query=self.query_one("#lookup", LookupInput).value,
            word=None if highlighted is None else str(words.get_option_at_index(highlighted).prompt),
            highlighted=highlighted,
            scroll_y=self.query_one("#definition", VerticalScroll).scroll_y,
            link=self._link,
        ))
        self._look_up(word)
        self.refresh_bindings()

    def _look_up(self, query: str) -> None:
        """Search for ``query`` as if it had been typed."""
        lookup = self.query_one("#lookup", LookupInput)
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None
        with lookup.prevent(Input.Changed):
            lookup.value = query
        lookup.cursor_position = len(query)
        self.search()

    def action_back(self) -> None:
        """Go back to where the last cross reference was followed from."""
        if not self._history:
            return
        place = self._history.pop()
        self.refresh_bindings()
        self._look_up(place.query)
        words = self.query_one("#words", OptionList)
        index = place.highlighted
        if (
            place.word is not None and index is not None and index < words.option_count
            and str(words.get_option_at_index(index).prompt) == place.word
        ):
            words.highlighted = index
            self.show_definition(place.word)
        # Restore the scroll position once the definition has been laid out.
        self.call_after_refresh(self._restore_place, place)

    def _restore_place(self, place: Place) -> None:
        pane = self.query_one("#definition", VerticalScroll)
        pane.scroll_to(y=place.scroll_y, animate=False, immediate=True)
        if place.link is not None and place.link < len(self._links):
            self._link = place.link
            self._update_definition()
            self.refresh_bindings()

    @on(mouse.MouseBack)
    def _mouse_back(self) -> None:
        if self.screen is self.screen_stack[0]:  # not in the command palette
            self.action_back()

    # -- clipboard ----------------------------------------------------------

    def action_copy_definition(self) -> None:
        text = self.definition
        if not text:
            self.notify("No definition to copy.", severity="warning")
            return
        mode = self.clipboard_mode
        if self.is_web:
            mode = "osc52"  # the browser front end turns OSC 52 into a clipboard write
        elif mode == "auto":
            if self._osc52 is None:
                self._osc52 = clipboard.terminal_supports_osc52()
            mode = "osc52" if self._osc52 else "system"
        if mode == "osc52":
            self._copy_osc52(text)
        else:
            self._copy_system(text, fallback=self.clipboard_mode == "auto")

    def _copy_osc52(self, text: str, note: str = "") -> None:
        self.copy_to_clipboard(text)
        self.notify(f"Copied definition to the clipboard (OSC 52){note}.")

    @work(thread=True, exclusive=True, group="clipboard", exit_on_error=False)
    def _copy_system(self, text: str, fallback: bool) -> None:
        try:
            tool = clipboard.system_copy(text)
        except clipboard.ClipboardError as e:
            if fallback:
                self.call_from_thread(self._copy_osc52, text, f"; {e}")
            else:
                self.call_from_thread(self.notify, f"Copy failed: {e}.", severity="error")
            return
        self.call_from_thread(self.notify, f"Copied definition to the clipboard ({tool}).")
