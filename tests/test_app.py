from __future__ import annotations

from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import OptionList

from pydict import clipboard
from pydict.app import PyDictApp
from pydict.stardict import parse_ifo
from stardict_writer import write_dictionary


def make_app(*ifos: Path, **kwargs) -> PyDictApp:
    return PyDictApp([parse_ifo(i) for i in ifos], **kwargs)


async def settle(pilot, app: PyDictApp) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause(0.2)
    await pilot.pause()


def words(app: PyDictApp) -> list[str]:
    option_list = app.query_one("#words", OptionList)
    return [str(option_list.get_option_at_index(i).prompt) for i in range(option_list.option_count)]


async def test_search_and_definition(fruit_ifo: Path, animals_ifo: Path) -> None:
    app = make_app(fruit_ifo, animals_ifo)
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press(*"apple")
        await settle(pilot, app)
        assert words(app) == ["Apple", "apple", "apples"]
        assert app.query_one("#words", OptionList).highlighted == 1
        assert "A round fruit." in app.definition
        assert "Animals" in app.definition and "Not an animal." in app.definition

        await pilot.press("down")  # moves the list without leaving the input
        await pilot.pause()
        assert app.focused.id == "lookup"
        assert "More than one apple." in app.definition


async def test_terminal_background_is_transparent(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="ap")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.ansi_color
        assert app.screen.styles.background.ansi == -1  # ansi_default
        for selector in ("#lookup", "#words", "#definition", "Footer"):
            assert app.query_one(selector).styles.background.ansi == -1, selector


@pytest.mark.parametrize("background", ["#fdf6e3", "#002b36"])
async def test_theme_follows_terminal_background(fruit_ifo: Path, background: str) -> None:
    from textual.color import Color

    from pydict import colors

    bg = Color.parse(background)
    app = make_app(fruit_ifo, word="ap", terminal_background=bg)
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.theme == colors.THEME_NAME
        assert app.current_theme.dark == colors.is_dark(bg)
        assert app.text_background == bg


@pytest.mark.parametrize("focus_color", [None, "#d33682"])
async def test_focus_color(fruit_ifo: Path, focus_color: str | None) -> None:
    from textual.color import Color

    app = make_app(
        fruit_ifo, word="ap", focus_color=Color.parse(focus_color) if focus_color else None
    )
    async with app.run_test() as pilot:
        await settle(pilot, app)
        variables = app.get_css_variables()
        app.query_one("#find-bar").display = True
        for selector, default in [
            ("#words", "accent"), ("#definition", "accent"), ("#lookup", "border"), ("#find", "border"),
        ]:
            expected = Color.parse(focus_color or variables[default])
            widget = app.query_one(selector)
            if widget.has_focus:
                app.query_one("#definition").focus()
                await pilot.pause()
            assert widget.styles.border_top[1] != expected, selector
            widget.focus()
            await pilot.pause()
            assert widget.styles.border_top[1] == expected, selector
        scrollbar = app.query_one("#definition").styles.scrollbar_color_active
        assert scrollbar == Color.parse(focus_color or variables["primary"])


async def test_initial_word_and_fuzzy(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="bananna")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        option_list = app.query_one("#words", OptionList)
        assert option_list.border_title == "Did you mean?"
        assert words(app) == ["banana"]
        assert "A long yellow fruit." in app.definition


async def test_no_dictionaries() -> None:
    app = PyDictApp([])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.definition == ""


async def test_tab_navigation(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="ap")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.focused.id == "lookup"
        await pilot.press("tab")
        assert app.focused.id == "words"
        await pilot.press("tab")
        assert app.focused.id == "definition"
        await pilot.press("tab")
        assert app.focused.id == "lookup"
        await pilot.press("shift+tab")
        assert app.focused.id == "definition"
        await pilot.press("shift+tab")
        assert app.focused.id == "words"


async def test_page_keys_scroll_definition(tmp_path: Path) -> None:
    long = "\n".join(f"line {n}" for n in range(200))
    ifo = write_dictionary(tmp_path, "long", {"long": long, "longer": long})
    app = make_app(ifo, word="long")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        pane = app.query_one("#definition", VerticalScroll)
        option_list = app.query_one("#words", OptionList)
        for focus in ("lookup", "words"):
            app.query_one(f"#{focus}").focus()
            await pilot.press("pagedown")
            await pilot.pause()
            assert pane.scroll_y > 0
            assert option_list.highlighted == 0
            await pilot.press("pageup")
            await pilot.pause()
            assert pane.scroll_y == 0


async def test_enter_follows_focus_option(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, focus="definition")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press(*"ap", "enter")
        await settle(pilot, app)
        assert app.focused.id == "definition"


async def test_click_into_lookup_selects_text(fruit_ifo: Path) -> None:
    from textual.widgets.input import Selection

    app = make_app(fruit_ifo, word="apple")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        lookup = app.query_one("#lookup")
        app.query_one("#words").focus()
        await pilot.click("#lookup", offset=(4, 1))
        assert app.focused is lookup
        assert lookup.selection == Selection(0, 5)  # like focusing with tab
        await pilot.click("#lookup", offset=(4, 1))  # already focused: moves the cursor
        assert lookup.selection.is_empty


async def test_enter_moves_focus(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo)
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.focused.id == "lookup"
        await pilot.press(*"ap")
        await settle(pilot, app)
        await pilot.press("enter")
        assert app.focused.id == "words"
        await pilot.press("down", "enter")
        assert app.focused.id == "definition"
        assert "A round fruit." in app.definition


async def test_mouse_focus(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="ap")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.click("#definition")
        assert app.focused is app.query_one("#definition", VerticalScroll)
        await pilot.click("#words", offset=(2, 3))
        assert app.focused.id == "words"
        await pilot.pause()
        assert "More than one apple." in app.definition
        await pilot.click("#lookup")
        assert app.focused.id == "lookup"


@pytest.mark.parametrize("focus", ["lookup", "words", "definition"])
async def test_escape_quits(fruit_ifo: Path, focus: str) -> None:
    app = make_app(fruit_ifo, word="ap")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        app.query_one(f"#{focus}").focus()
        await pilot.press("escape")
        await pilot.pause()
        assert not app.is_running


async def test_escape_closes_command_palette_first(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo)
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press("ctrl+p")
        await pilot.pause(0.2)
        assert app.screen is not app.screen_stack[0]
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.is_running
        assert app.screen is app.screen_stack[0]


async def test_copy_osc52(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="banana", clipboard_mode="osc52")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press("ctrl+c")
        assert app.clipboard == app.definition
        assert "A long yellow fruit." in app.clipboard
        assert "\x1b" not in app.clipboard  # plain text


async def test_copy_auto_uses_system_tool(fruit_ifo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copied = []
    monkeypatch.setattr(clipboard, "terminal_supports_osc52", lambda: False)
    monkeypatch.setattr(clipboard, "system_copy", lambda text: copied.append(text) or "fake")
    app = make_app(fruit_ifo, word="banana")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press("ctrl+c")
        await settle(pilot, app)
        assert copied == [app.definition]
        assert app.clipboard == ""


async def test_copy_auto_falls_back_to_osc52(fruit_ifo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(text):
        raise clipboard.ClipboardError("no tool")

    monkeypatch.setattr(clipboard, "terminal_supports_osc52", lambda: False)
    monkeypatch.setattr(clipboard, "system_copy", fail)
    app = make_app(fruit_ifo, word="banana")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press("ctrl+c")
        await settle(pilot, app)
        assert app.clipboard == app.definition


async def test_copy_when_input_has_selection(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="banana", clipboard_mode="osc52")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press("shift+home")  # select the text in the lookup box
        await pilot.press("ctrl+c")
        assert app.clipboard == app.definition


async def test_copy_selected_definition_text(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="banana", clipboard_mode="osc52")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        static = app.query_one("#definition-text")
        line = app.definition.split("\n").index("A long yellow fruit.")
        await pilot.mouse_down(static, offset=(2, line))
        await pilot.hover(static, offset=(8, line))
        await pilot.mouse_up(static, offset=(8, line))
        assert app.query_one("#definition").has_focus
        await pilot.press("ctrl+c")
        assert app.clipboard == "long ye"
        # Without focus, the whole definition is copied.
        app.query_one("#words").focus()
        await pilot.press("ctrl+c")
        assert app.clipboard == app.definition


async def test_copy_nothing(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, clipboard_mode="osc52")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        await pilot.press("ctrl+c")
        assert app.clipboard == ""


@pytest.mark.parametrize("no_color", [False, True])
async def test_no_color(fruit_ifo: Path, monkeypatch: pytest.MonkeyPatch, no_color: bool) -> None:
    from textual.filter import Monochrome, NoColor

    # Textual's own NO_COLOR handling is replaced by the no_color argument.
    monkeypatch.setenv("NO_COLOR", "")
    app = make_app(fruit_ifo, word="ap", no_color=no_color)
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.no_color is no_color
        assert not any(isinstance(f, Monochrome) for f in app._filters)
        assert any(isinstance(f, NoColor) for f in app._filters) is no_color
        words = app.query_one("#words", OptionList)
        highlighted = words.get_component_rich_style("option-list--option-highlighted")
        assert bool(highlighted.reverse) is no_color
        await pilot.press("tab")
        highlighted = words.get_component_rich_style("option-list--option-highlighted")
        assert bool(highlighted.reverse) is no_color


@pytest.fixture
def linked_ifo(tmp_path: Path) -> Path:
    filler = "".join(f"<p>line {n}</p>" for n in range(80))
    return write_dictionary(tmp_path, "linked", {
        "ran": '<p>past of <a href="bword://run">run</a> and <a href="bword://rin">rin</a></p>',
        "rin": "<p>to run</p>",
        "run": "<p>to move swiftly</p>",
        "long": f'<p><a href="bword://ran">top</a></p>{filler}<p><a href="bword://rin">bottom</a></p>',
    }, sametypesequence="h")


async def test_click_link_looks_up_word(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="ran")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert "past of run and rin" in app.definition
        lines = app.definition.split("\n")
        y = next(n for n, line in enumerate(lines) if line.startswith("past of"))
        await pilot.click("#definition-text", offset=(lines[y].index("run"), y))
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "run"
        assert "to move swiftly" in app.definition
        assert words(app)[app.query_one("#words", OptionList).highlighted] == "run"


async def test_double_click_looks_up_word(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="rin")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        lines = app.definition.split("\n")
        y = lines.index("to run")
        await pilot.double_click("#definition-text", offset=(2, y))  # the space
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "rin"
        await pilot.double_click("#definition-text", offset=(lines[y].index("run") + 1, y))
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "run"
        assert "to move swiftly" in app.definition
        assert not app.screen.selections  # rather than selecting all the text
        await pilot.press("backspace")  # back like from a cross reference
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "rin"
        assert app.check_action("back", ()) is False


async def test_double_click_link_follows_it_once(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="ran")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        lines = app.definition.split("\n")
        y = next(n for n, line in enumerate(lines) if line.startswith("past of"))
        await pilot.double_click("#definition-text", offset=(lines[y].index("run"), y))
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "run"
        assert len(app._history) == 1


async def test_keyboard_link_cursor(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="ran")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        app.query_one("#definition").focus()
        await pilot.press("enter")  # nothing selected yet
        assert app.query_one("#lookup").value == "ran"
        await pilot.press("right", "right", "right")  # stops at the last link
        assert app._link == 1
        await pilot.press("left")
        assert app._link == 0
        await pilot.press("enter")
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "run"
        assert app.focused.id == "definition"
        assert app._link is None and app._links == []
        assert "to move swiftly" in app.definition


async def test_link_cursor_follows_scrolling(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="long")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        pane = app.query_one("#definition", VerticalScroll)
        pane.focus()
        await pilot.press("right")
        assert app._link == 0 and pane.scroll_y == 0
        await pilot.press("right")  # off screen: scrolls to it
        await pilot.pause()
        assert app._link == 1 and pane.scroll_y > 0
        assert app._lines_of_links()[1] in app._visible_lines()
        await pilot.press("home")
        await pilot.wait_for_scheduled_animations()
        assert pane.scroll_y == 0
        assert app._link is None  # scrolled out of view
        await pilot.press("right")  # starts again from the visible links
        assert app._link == 0


async def test_link_bindings_only_with_links(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="run")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.check_action("select_link", (1,)) is False
        assert app.check_action("follow_selected_link", ()) is False
        app.action_follow_link("ran")
        await settle(pilot, app)
        assert app.check_action("select_link", (1,)) is True


async def test_backspace_goes_back_through_followed_links(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="ran")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        pane = app.query_one("#definition", VerticalScroll)
        pane.focus()
        await pilot.press("backspace")  # nothing to go back to
        assert app.query_one("#lookup").value == "ran"
        assert app.check_action("back", ()) is False
        await pilot.press("right", "right", "enter")  # ran -> rin
        await settle(pilot, app)
        assert "to run" in app.definition
        app.action_follow_link("run")  # rin -> run
        await settle(pilot, app)
        assert "to move swiftly" in app.definition
        assert app.check_action("back", ()) is True

        await pilot.press("backspace")
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "rin"
        assert "to run" in app.definition
        app.query_one("#words").focus()  # works from the word list too
        await pilot.press("backspace")
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "ran"
        assert "past of run and rin" in app.definition
        assert app._link == 1  # the link that was followed is selected again
        await pilot.press("backspace")  # back at the start
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "ran"


async def test_back_restores_highlighted_word_and_scroll(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="r")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert words(app) == ["ran", "rin", "run"]
        app.query_one("#words").focus()
        await pilot.press("down")  # highlight "rin" without typing it
        app.action_follow_link("long")
        await settle(pilot, app)
        pane = app.query_one("#definition", VerticalScroll)
        pane.focus()
        await pilot.press("end")
        await pilot.wait_for_scheduled_animations()
        scrolled = pane.scroll_y
        assert scrolled > 0
        await pilot.press("right", "enter")  # long -> rin, from the bottom
        await settle(pilot, app)
        await pilot.press("backspace")
        await settle(pilot, app)
        assert "line 79" in app.definition and pane.scroll_y == scrolled
        await pilot.press("backspace")
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "r"
        assert app.query_one("#words", OptionList).highlighted == 1
        assert "to run" in app.definition


async def test_typing_clears_history(linked_ifo: Path) -> None:
    app = make_app(linked_ifo, word="ran")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        app.action_follow_link("run")
        await settle(pilot, app)
        app.query_one("#lookup").focus()
        await pilot.press("end", "backspace")  # edits the lookup box, doesn't go back
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "ru"
        assert app._history == []


async def test_mouse_back_button(linked_ifo: Path) -> None:
    from pydict.mouse import MouseBack

    app = make_app(linked_ifo, word="ran")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        app.action_follow_link("run")
        await settle(pilot, app)
        app.post_message(MouseBack())
        await settle(pilot, app)
        assert app.query_one("#lookup").value == "ran"


def test_parser_reports_back_button() -> None:
    from textual import events
    from textual._xterm_parser import XTermParser

    from pydict import mouse

    mouse.install()
    mouse.install()  # only patches once
    parser = XTermParser()

    def parse(data: str) -> list:
        return list(parser.feed(data))

    [back] = parse("\x1b[<128;5;5M")
    assert isinstance(back, mouse.MouseBack)
    assert isinstance(parse("\x1b[<144;5;5M")[0], mouse.MouseBack)  # with ctrl
    for code in ("\x1b[<128;5;5m", "\x1b[<129;5;5M", "\x1b[<129;5;5m", "\x1b[<160;6;5M"):
        assert parse(code) == [], code  # release, forward button, drag
    [click] = parse("\x1b[<0;5;5M")
    assert isinstance(click, events.MouseDown) and click.button == 1


@pytest.mark.parametrize(("word", "focus", "expected"), [
    ("ap", None, "lookup"),
    ("ap", "words", "words"),
    ("ap", "definition", "definition"),
    ("", "words", "lookup"),  # nothing looked up
    ("zzzzzz", "definition", "lookup"),  # nothing found
])
async def test_initial_focus(fruit_ifo: Path, word: str, focus: str | None, expected: str) -> None:
    app = make_app(fruit_ifo, word=word, focus=focus)
    async with app.run_test() as pilot:
        await settle(pilot, app)
        assert app.focused.id == expected


@pytest.fixture
def findable_ifo(tmp_path: Path) -> Path:
    lines = [f"line {n}" for n in range(100)]
    lines[0], lines[50], lines[99] = "Pear at the top", "a pear in the middle", "PEAR at the bottom"
    return write_dictionary(tmp_path, "findable", {"fruits": "\n".join(lines)})


def match_lines(app: PyDictApp) -> list[int]:
    return app._lines_of("matches")


async def test_find_highlights_as_you_type(findable_ifo: Path) -> None:
    app = make_app(findable_ifo, word="fruits")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        bar = app.query_one("#find-bar")
        assert not bar.display
        app.query_one("#definition").focus()
        await pilot.press("slash")
        assert bar.display and app.focused.id == "find"
        await pilot.press("p")
        assert len(app._matches) > 3  # "p" is everywhere
        await pilot.press(*"ear")
        plain = app.definition
        assert [plain[s:e] for s, e in app._matches] == ["Pear", "pear", "PEAR"]  # ignores case
        assert app._match == 0
        assert str(app.query_one("#find-count").render()) == "1/3"
        highlighted = app.query_one("#definition-text").content
        start, end = app._matches[1]
        assert any(
            span.start <= start and span.end >= end and span.style.bgcolor is not None
            for span in highlighted.spans
        )

        await pilot.press("enter")  # closes the box, keeps the matches
        assert not bar.display and app.focused.id == "definition"
        assert len(app._matches) == 3


async def test_find_steps_wrap(findable_ifo: Path) -> None:
    app = make_app(findable_ifo, word="fruits")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        pane = app.query_one("#definition", VerticalScroll)
        pane.focus()
        await pilot.press("slash", *"pear")
        await pilot.press("down")
        assert app._match == 1
        assert match_lines(app)[1] in app._visible_lines() and pane.scroll_y > 0
        await pilot.press("down", "down")  # wraps around the bottom
        assert app._match == 0 and pane.scroll_y == 0
        await pilot.press("up")  # and the top
        assert app._match == 2
        assert match_lines(app)[2] in app._visible_lines()

        await pilot.click("#find-next")
        assert app._match == 0 and app.focused.id == "find"
        await pilot.click("#find-previous")
        assert app._match == 2

        await pilot.press("enter")
        await pilot.press("n")  # works with the box closed
        assert app._match == 0
        await pilot.press("n")
        assert app._match == 1
        await pilot.press("N")
        assert app._match == 0


async def test_find_starts_from_view(findable_ifo: Path) -> None:
    app = make_app(findable_ifo, word="fruits")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        pane = app.query_one("#definition", VerticalScroll)
        pane.focus()
        pane.scroll_to(y=30, animate=False, immediate=True)
        await pilot.press("slash", *"pear")
        assert app._match == 1  # the first one below the top of the view
        await pilot.press("enter")
        await pilot.press("home")
        await pilot.wait_for_scheduled_animations()
        await pilot.press("n")  # the current match is out of view
        assert app._match == 0


async def test_find_escape_clears(findable_ifo: Path) -> None:
    app = make_app(findable_ifo, word="fruits")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        app.query_one("#definition").focus()
        await pilot.press("slash", *"pear", "escape")
        assert app.is_running  # escape doesn't quit from the find box
        assert not app.query_one("#find-bar").display
        assert app._matches == [] and app.focused.id == "definition"
        assert app.check_action("find_step", (1,)) is False
        await pilot.press("slash")  # the last search comes back
        await pilot.pause()
        assert len(app._matches) == 3
        assert app.query_one("#find").selected_text == "pear"


async def test_find_no_matches(findable_ifo: Path) -> None:
    app = make_app(findable_ifo, word="fruits")
    async with app.run_test() as pilot:
        await settle(pilot, app)
        app.query_one("#definition").focus()
        await pilot.press("slash", *"plum")
        assert str(app.query_one("#find-count").render()) == "No matches"
        await pilot.press("down", "enter")
        assert not app.query_one("#find-bar").display


@pytest.mark.parametrize(("background", "color", "bgcolor"), [
    ("#002b36", "#000000", "#ffffff"),
    ("#fdf6e3", "#ffffff", "#000000"),
])
async def test_find_highlight_colors(findable_ifo: Path, background: str, color: str, bgcolor: str) -> None:
    from textual.color import Color

    app = make_app(findable_ifo, word="fruits", terminal_background=Color.parse(background))
    async with app.run_test() as pilot:
        await settle(pilot, app)
        style = app._match_style()
        assert (style.color.name, style.bgcolor.name) == (color, bgcolor)
