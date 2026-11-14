# pydict

A StarDict dictionary viewer for the terminal and the browser, built with
[Textual](https://textual.textualize.io/).

## Install

```sh
pipx install .            # terminal app
pipx install '.[web]'     # with browser mode
```

## Usage

```sh
pydict                    # search all dictionaries sdcv would find
pydict serendipity        # start with a word looked up
pydict -l                 # list dictionaries
pydict -d ~/dicts/oed     # use only this dictionary (no search)
pydict --serve            # serve the app at http://localhost:8000
```

Type in the lookup box to fill the word list with matching words; when nothing
starts with what you typed, close spellings are offered instead. The
definition of the highlighted word is shown on the right.

| Key | Action |
| --- | --- |
| tab / shift+tab | move between the lookup box, word list and definition |
| up / down (in the lookup box) | move through the word list |
| page up / page down | scroll the definition |
| enter | lookup box → word list → definition |
| left / right (in the definition) | select the previous / next cross reference |
| enter (on a cross reference) | look up the word it points to |
| backspace (in the word list or definition) | go back to where the last cross reference was followed from |
| ctrl+c | copy the displayed definition as plain text |
| ctrl+q / escape | quit |

Panes can also be focused with the mouse, and clicking a cross reference
looks it up. Cross references are `bword://` links in HTML dictionaries and
`<kref>` tags in XDXF ones. The mouse's back button goes back like backspace
does, in terminals that report it (pydict reads xterm's button 8). Typing a new
word in the lookup box starts over.

### Finding dictionaries

Like `sdcv`, pydict searches recursively in:

1. `~/.stardict/dic` and `$XDG_DATA_HOME/stardict/dic` (sdcv only uses the
   second when `~/.stardict` doesn't exist; pydict always searches both);
2. `--data-dir`, else `$STARDICT_DATA_DIR`, else `/usr/share/stardict/dic`.

`-x/--only-data-dir` skips the user directory, and `-u/--use-dict NAME`
uses only dictionaries whose bookname contains `NAME`, ignoring case
(`-u oxford` picks every Oxford dictionary). Booknames listed in
`$XDG_CONFIG_HOME/sdcv_ordering` or `~/.sdcv_ordering` are shown first.

`-d/--dict PATH` skips the search. `PATH` can be a dictionary's `.ifo` file,
any of its other files, or a directory of dictionaries.

To make startup fast, the index offsets of big dictionaries are cached in
`$XDG_CACHE_HOME/pydict` (`--no-cache` turns this off).

### Clipboard

`ctrl+c` uses the OSC 52 terminal escape when the terminal is known to
support it: kitty, WezTerm, foot, Alacritty, Ghostty, iTerm2, Windows
Terminal, and tmux when its `set-clipboard` option is on and the outer
terminal supports OSC 52. Otherwise it uses `wl-copy`, `xclip`, `xsel`,
`pbcopy` or `clip`. Use `--clipboard osc52|system|auto` or `$PYDICT_CLIPBOARD`
to override this. In browser mode, the copy goes to the browser's clipboard;
browsers only allow that on `localhost` or over HTTPS.

### Colors

`--no-color`, or a non-empty `NO_COLOR` environment variable
([no-color.org](https://no-color.org)), turns colors off. The highlighted word
and the dictionary headers are then shown in reverse video, and the focused
pane gets a heavier border.

In a terminal, pydict draws on the terminal's own background. It asks the
terminal for its colors at startup (falling back to `$COLORFGBG`, then
assuming a dark background) and picks text and UI colors that stay readable
on it. Colors from dictionary markup are lightened or darkened where needed
to reach a WCAG contrast ratio of at least 4.5:1. If your terminal doesn't
answer the query, pass `--background dark`, `--background light` or
`--background '#fdf6e3'`.

tmux draws italics as reverse video when its `default-terminal` is `screen`
or `screen-*`, so pydict leaves italics out there. To get italics back (in
every program), add `set -g default-terminal "tmux-256color"` to your tmux
config.

## Supported formats

`.ifo` + `.idx` (or `.idx.gz`) + `.dict` (or `.dict.dz`), with optional `.syn`
synonym files and 64-bit offsets. Plain text, XDXF, HTML and Pango markup are
rendered; images and sounds are shown as placeholders.

## Development

```sh
uv venv && uv pip install -e . --group dev
.venv/bin/pytest
```
