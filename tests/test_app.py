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


async def test_enter_moves_focus(fruit_ifo: Path) -> None:
    app = make_app(fruit_ifo, word="ap")
    async with app.run_test() as pilot:
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
