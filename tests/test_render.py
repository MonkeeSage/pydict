from __future__ import annotations

from rich.style import Style

from pydict.render import render_entry, render_field, render_markup
from pydict.stardict import Entry


def styles_of(text, substring):
    start = text.plain.index(substring)
    return [s.style for s in text.spans if s.start <= start < s.end]


def test_plain_text() -> None:
    assert render_field("m", b"hello\nworld\n").plain == "hello\nworld"


def test_xdxf() -> None:
    text = render_markup(
        '<k>cat</k>\n<abr>n.</abr> <tr>kat</tr> a <c c="red">small</c> <ex>the cat sat</ex>'
        "\n<blockquote>quoted</blockquote>after",
        "x",
    )
    assert text.plain == "cat\nn. [kat] a small the cat sat\n  quoted\nafter"
    assert any(isinstance(s, Style) and s.bold for s in styles_of(text, "cat"))
    assert any(isinstance(s, Style) and s.color and s.color.triplet == (255, 0, 0)
               for s in styles_of(text, "small"))


def test_markup_colors_made_readable() -> None:
    from textual.color import Color

    from pydict.colors import MIN_CONTRAST, contrast_ratio

    markup = '<c c="darkslategray">pron</c> <font color="#ffff80">pale</font>'
    for background in (Color(0, 0, 0), Color(255, 255, 255)):
        text = render_markup(markup, "x", background)
        for word in ("pron", "pale"):
            [style] = [s for s in styles_of(text, word) if s.color]
            r, g, b = style.color.triplet
            assert contrast_ratio(Color(r, g, b), background) >= MIN_CONTRAST - 0.01
    # Without a background, colors are kept as given.
    [style] = [s for s in styles_of(render_markup(markup, "x"), "pron") if s.color]
    assert style.color.triplet == (47, 79, 79)


def test_html() -> None:
    text = render_markup(
        "<p>first   para</p><ul><li>one</li><li>two</li></ul><ol><li>x</li></ol>"
        "a<br>b<script>ignored()</script> &amp; <img alt='pic'>",
        "h",
    )
    assert text.plain == "first para\n\n• one\n• two\n1. x\na\nb & [image: pic]"


def test_html_spacing() -> None:
    # Wiktionary's layout: part of speech headings in paragraphs, and the
    # examples and quotations of a sense in a quote.
    text = render_markup(
        "<div>Etymology</div><br><p><i>verb</i> <b>run</b></p>"
        '<div class="sense"><b>1.</b> To move.<blockquote><div><i>Run!</i></div>'
        "<div><small>1967, Someone:</small> <i>He ran.</i></div></blockquote></div>"
        '<div class="sense"><b>2.</b> To flow.</div><br><p><i>noun</i> <b>run</b></p>'
        "<p>first</p><br><br><p>second</p>a<br>b<br><br>c",
        "h",
    )
    assert text.plain == (
        "Etymology\n\nverb run\n\n1. To move.\n\n  Run!\n\n  1967, Someone: He ran.\n\n"
        "2. To flow.\n\nnoun run\n\nfirst\n\nsecond\n\na\nb\n\nc"
    )


def test_bad_markup_does_not_crash() -> None:
    assert render_markup("<b>unclosed <i>tags</b> </x> &bogus;", "h").plain == "unclosed tags &bogus;"


def test_binary_fields() -> None:
    assert render_field("W", b"\0\1").plain == "[audio]"
    assert render_field("P", b"\0\1").plain == "[image]"


def test_entry_headword() -> None:
    assert render_entry(Entry("cat", [("m", b"a pet")])).plain == "cat\na pet"
    assert render_entry(Entry("cat", [("x", b"<k>cat</k> a pet")])).plain == "cat a pet"
    synonym = render_entry(Entry("cat", [("m", b"a pet")], synonym="kitty"))
    assert synonym.plain == "cat  (from kitty)\na pet"


def _palette(background: str, foreground: str):
    from textual.color import Color

    from pydict.render import Palette

    return Palette(Color.parse(background), Color.parse(foreground), Color.parse("#0178d4"))


def test_palette_replaces_terminal_dependent_styles() -> None:
    from textual.color import Color

    from pydict.colors import MIN_CONTRAST, contrast_ratio

    for background, foreground in (("#1e1e1e", "#d0d0d0"), ("#fdf6e3", "#333333")):
        palette = _palette(background, foreground)
        bg = Color.parse(background)
        header = palette.resolve("bold reverse")
        assert not header.reverse and header.bold
        assert contrast_ratio(Color(*header.color.triplet), Color(*header.bgcolor.triplet)) >= MIN_CONTRAST
        muted = palette.resolve(Style(italic=True, dim=True))
        assert not muted.dim and muted.italic
        assert contrast_ratio(Color(*muted.color.triplet), bg) >= MIN_CONTRAST - 0.01
        # Dimmed explicit colors stay readable too.
        dim_red = palette.resolve(Style(color="#ff0000", dim=True))
        assert not dim_red.dim
        assert contrast_ratio(Color(*dim_red.color.triplet), bg) >= MIN_CONTRAST - 0.01
        # Terminal palette colors are kept, just not dimmed.
        assert palette.resolve(Style(color="green", dim=True)) == Style(color="green", dim=False)


def test_render_results_with_palette(fruit_ifo, animals_ifo) -> None:
    from pydict.render import render_results
    from pydict.stardict import Dictionary, Library

    lib = Library([Dictionary.from_ifo(fruit_ifo), Dictionary.from_ifo(animals_ifo)])
    lib.open()
    entries = [Entry("x", [("x", b"<ex>an example</ex> <co>comment</co>")], synonym="y")]
    results = lib.lookup("apple")
    results[0].entries += entries
    text = render_results(results, _palette("#1e1e1e", "#d0d0d0"))
    styles = [s.style for s in text.spans]
    assert styles and all(isinstance(s, Style) for s in styles)
    assert not any(s.dim or s.reverse for s in styles)
    plain = render_results(results)
    assert any(isinstance(s.style, str) and "reverse" in s.style for s in plain.spans)


def test_palette_without_color() -> None:
    from dataclasses import replace

    palette = replace(_palette("#1e1e1e", "#d0d0d0"), color=False)
    assert palette.resolve("bold reverse") == Style(bold=True, reverse=True)
    assert palette.resolve(Style(color="#ff0000", bgcolor="blue", dim=True)) == Style(dim=True)
    assert palette.resolve(Style(italic=True, color="green")) == Style(italic=True)
    no_italics = replace(palette, italics=False)
    assert no_italics.resolve(Style(italic=True, color="green")) == Style(italic=False)


def test_palette_without_italics() -> None:
    from dataclasses import replace

    palette = _palette("#1e1e1e", "#d0d0d0")
    assert palette.resolve(Style(italic=True, color="green")).italic
    no_italics = replace(palette, italics=False)
    assert no_italics.resolve(Style(italic=True, color="green")) == Style(italic=False, color="green")
    assert not no_italics.resolve(Style(italic=True, dim=True)).italic


def test_bword_links() -> None:
    from pydict.render import LINK_TARGET, find_links

    text = render_markup(
        '<div>past of <a href="bword://run">run</a>, see <a href="bword://ice%20cream"> ice cream </a>'
        ' or <a href="https://example.com">the web</a></div>',
        "h",
    )
    assert text.plain == "past of run, see ice cream or the web"
    links = find_links(text)
    assert [(text.plain[link.start:link.end], link.target) for link in links] == [
        ("run", "run"), ("ice cream", "ice cream"),
    ]
    # A click runs the app's follow_link action.
    [style] = [s.style for s in text.spans if s.start == links[0].start and LINK_TARGET in s.style.meta]
    assert style.meta["@click"] == ("app.follow_link", ("run",))
    assert style.underline


def test_xdxf_kref_links() -> None:
    from pydict.render import find_links

    text = render_markup("<k>ate</k> obs. f. <kref>oat</kref>, <i><kref>hate</kref></i>", "x")
    assert [link.target for link in find_links(text)] == ["oat", "hate"]
    unclosed = render_markup("see <kref>oat", "x")
    assert [link.target for link in find_links(unclosed)] == ["oat"]


def test_links_survive_entry_results_and_palette(fruit_ifo) -> None:
    from dataclasses import replace

    from pydict.render import find_links, render_results
    from pydict.stardict import Dictionary, Library

    lib = Library([Dictionary.from_ifo(fruit_ifo)])
    lib.open()
    results = lib.lookup("apple")
    results[0].entries.append(Entry("x", [("h", b'see <a href="bword://banana">banana</a>')]))
    palette = _palette("#1e1e1e", "#d0d0d0")
    for p in (palette, replace(palette, color=False)):
        text = render_results(results, p)
        [link] = find_links(text)
        assert text.plain[link.start:link.end] == "banana" and link.target == "banana"
