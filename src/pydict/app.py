"""The pydict Textual application."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.color import Color
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.filter import Monochrome, NoColor
from textual.timer import Timer
from textual.widgets import Footer, Input, OptionList, Static

from pydict import clipboard, colors
from pydict.colors import readable
from pydict.render import Palette, render_results
from pydict.stardict import DictInfo, Dictionary, DictionaryError, Library

SEARCH_DELAY = 0.12
WORD_LIMIT = 200


# Page up/down scroll the definition from any pane.
PAGE_BINDINGS = [
    Binding("pagedown", "app.scroll_definition('page_down')", show=False),
    Binding("pageup", "app.scroll_definition('page_up')", show=False),
]


class LookupInput(Input):
    """The lookup box. Up/down move through the word list without leaving it."""

    BINDINGS = [
        Binding("down", "word_list('cursor_down')", show=False),
        Binding("up", "word_list('cursor_up')", show=False),
        *PAGE_BINDINGS,
    ]

    def action_word_list(self, action: str) -> None:
        words = self.app.query_one("#words", OptionList)
        getattr(words, f"action_{action}")()


class WordList(OptionList):
    """The word list. Enter moves on to the definition (a click doesn't)."""

    BINDINGS = [
        Binding("enter", "app.focus('definition')", "Definition", show=False),
        *PAGE_BINDINGS,
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
        clipboard_mode: str = "auto",
        cache_dir: Path | None = None,
        warnings: list[str] | None = None,
        terminal_background: Color | None = None,
        terminal_foreground: Color | None = None,
        no_color: bool = False,
    ) -> None:
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
        self._shown_word: str | None = None
        self._search_timer: Timer | None = None
        self._osc52: bool | None = None

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return {colors.BORDER_VARIABLE: "#45505a"}

    def compose(self) -> ComposeResult:
        yield LookupInput(placeholder="Look up a word…", id="lookup")
        with Horizontal(id="results"):
            yield WordList(id="words")
            with VerticalScroll(id="definition"):
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

    # -- searching ----------------------------------------------------------

    @on(Input.Changed, "#lookup")
    def _lookup_changed(self) -> None:
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
            words.focus()

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
        self.query_one("#definition-text", Static).update(text)
        self.query_one("#definition", VerticalScroll).scroll_home(animate=False)

    def action_scroll_definition(self, action: str) -> None:
        pane = self.query_one("#definition", VerticalScroll)
        getattr(pane, f"action_{action}")()

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
