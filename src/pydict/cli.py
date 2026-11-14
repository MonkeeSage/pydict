"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from pydict import __version__, clipboard, colors
from pydict.stardict import DictInfo, DictionaryError, discover, parse_ifo, resolve_dict_path
from pydict.stardict.discovery import select_dicts
from pydict.stardict.index import default_cache_dir

EPILOG = """\
Dictionaries are found much like sdcv finds them: recursively in
~/.stardict/dic and $XDG_DATA_HOME/stardict/dic, then in --data-dir,
$STARDICT_DATA_DIR or /usr/share/stardict/dic.
Booknames listed in $XDG_CONFIG_HOME/sdcv_ordering or ~/.sdcv_ordering are
used first. With --dict, no search is done and only the given dictionaries
are used.

Keys: tab/shift+tab move between panes, ctrl+c copies the definition,
ctrl+q or escape quits.
"""


def background_arg(value: str):
    try:
        return colors.parse_background(value)
    except Exception:
        raise argparse.ArgumentTypeError(f"not a color: {value!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydict",
        description="Look up words in StarDict dictionaries.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("word", nargs="?", default="", help="word to look up on start")
    parser.add_argument(
        "-d", "--dict", action="append", default=[], metavar="PATH", dest="dicts",
        help="use only this dictionary (its .ifo, any of its files, or a directory "
        "containing dictionaries); may be repeated",
    )
    parser.add_argument(
        "-2", "--data-dir", metavar="DIR",
        help="use this directory as the StarDict data directory",
    )
    parser.add_argument(
        "-x", "--only-data-dir", action="store_true",
        help="only use the dictionaries in --data-dir, not the user and system directories",
    )
    parser.add_argument(
        "-u", "--use-dict", action="append", default=[], metavar="NAME", dest="use_dicts",
        help="only use dictionaries whose bookname contains NAME, ignoring case "
        "(e.g. -u oxford); may be repeated",
    )
    parser.add_argument(
        "-l", "--list-dicts", action="store_true", help="list the dictionaries and exit"
    )
    parser.add_argument(
        "--clipboard", choices=clipboard.MODES, default=None,
        help="how ctrl+c copies: OSC 52 terminal escape, a system tool, or auto-detect "
        f"(default: ${clipboard.ENV_VAR} or auto)",
    )
    parser.add_argument(
        "--background", metavar="COLOR", type=background_arg,
        help="the terminal's background color, used to keep text readable: dark, light "
        "or a color such as '#fdf6e3' (default: ask the terminal, then $COLORFGBG)",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="don't use colors (also when $NO_COLOR is set and not empty)",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="don't cache index offsets on disk"
    )
    web = parser.add_argument_group("browser mode (needs pydict[web])")
    web.add_argument("--serve", action="store_true", help="serve the app to a web browser")
    web.add_argument("--host", default="localhost", help="host to serve on (default: %(default)s)")
    web.add_argument("--port", type=int, default=8000, help="port to serve on (default: %(default)s)")
    web.add_argument("--public-url", help="public URL, when behind a proxy")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def find_dictionaries(args: argparse.Namespace) -> tuple[list[DictInfo], list[str]]:
    if args.dicts:
        infos: list[DictInfo] = []
        for path in args.dicts:
            for ifo in resolve_dict_path(path):
                infos.append(parse_ifo(ifo))
        if args.use_dicts:
            return select_dicts(infos, args.use_dicts)
        return infos, []
    found = discover(args.data_dir, args.only_data_dir, args.use_dicts or None)
    return found.dictionaries, found.warnings


def list_dictionaries(infos: list[DictInfo]) -> None:
    header = "Dictionary's name"
    width = max([len(header), *(len(i.bookname) for i in infos)])
    print(f"{header:{width}}   Word count")
    for info in infos:
        print(f"{info.bookname:{width}}   {info.wordcount}")


def serve_command(args: argparse.Namespace) -> str:
    """The command textual-serve runs for each browser session."""
    argv = [sys.executable, "-m", "pydict"]
    for path in args.dicts:
        argv += ["--dict", str(Path(path).expanduser().resolve())]
    if args.data_dir:
        argv += ["--data-dir", str(Path(args.data_dir).expanduser().resolve())]
    if args.only_data_dir:
        argv.append("--only-data-dir")
    for name in args.use_dicts:
        argv += ["--use-dict", name]
    if args.no_cache:
        argv.append("--no-cache")
    if args.no_color:
        argv.append("--no-color")
    if args.word:
        argv += ["--", args.word]
    return shlex.join(argv)


def serve(args: argparse.Namespace) -> int:
    try:
        from textual_serve.server import Server
    except ImportError:
        print("pydict: browser mode needs textual-serve: pip install 'pydict[web]'", file=sys.stderr)
        return 1
    Server(
        serve_command(args), host=args.host, port=args.port,
        title="pydict", public_url=args.public_url,
    ).serve()
    return 0


def no_color_requested(args: argparse.Namespace, env=os.environ) -> bool:
    """``--no-color``, or ``$NO_COLOR`` set to anything but "" (https://no-color.org)."""
    return args.no_color or bool(env.get("NO_COLOR"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.only_data_dir and not args.data_dir and not args.dicts:
        parser.error("--only-data-dir needs --data-dir")
    try:
        clipboard_mode = clipboard.resolve_mode(args.clipboard)
    except ValueError as e:
        parser.error(str(e))

    try:
        infos, warnings = find_dictionaries(args)
    except DictionaryError as e:
        print(f"pydict: {e}", file=sys.stderr)
        return 1

    if args.list_dicts:
        for warning in warnings:
            print(f"pydict: {warning}", file=sys.stderr)
        list_dictionaries(infos)
        return 0
    if args.serve:
        return serve(args)

    from pydict.app import PyDictApp

    no_color = no_color_requested(args)
    if no_color or args.background is not None:
        foreground, background = None, args.background
    else:
        foreground, background = colors.detect_terminal_colors()
    app = PyDictApp(
        infos,
        word=args.word,
        clipboard_mode=clipboard_mode,
        cache_dir=None if args.no_cache else default_cache_dir(),
        warnings=warnings,
        terminal_background=background,
        terminal_foreground=foreground,
        no_color=no_color,
    )
    app.run()
    return app.return_code or 0
