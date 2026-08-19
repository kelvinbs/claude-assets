#!/usr/bin/env python3
"""lib-init — create the symbol library a board project owns.

    lib-init.py <board-dir> [--nickname NAME]

Outside the chain, like db-init. It makes the empty library and the
`sym-lib-table` entry that resolves it, so `symbol-draw` only ever merges
into a library that is already there and already reachable.

Section 2 of `board-build-tool.md` is what it satisfies: the library lives
in `lib/`, the table sits in the project directory, and every path in it is
`${KIPRJMOD}`-relative so a fresh clone opens with nothing missing.

It adds what is missing and leaves what is there.
"""

import argparse
import re
import sys
from pathlib import Path

SYM_LIB_VERSION = 20251024

EMPTY = (
    "(kicad_symbol_lib\n"
    f"\t(version {SYM_LIB_VERSION})\n"
    '\t(generator "lib-init.py")\n'
    '\t(generator_version "10.0")\n'
    ")\n"
)

TABLE = "sym-lib-table"


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"lib-init: {message}")


def nickname_of(board, given):
    """The nickname is the project, per section 2. Taken from the .kicad_pro
    when there is exactly one, because guessing it from a directory name is
    how two projects end up sharing an entry."""
    if given:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", given):
            raise Bad(f"'{given}' is not usable as a nickname")
        return given
    pro = sorted(board.glob("*.kicad_pro"))
    if len(pro) == 1:
        return pro[0].stem
    if not pro:
        raise Bad(f"no .kicad_pro in {board}. Give --nickname")
    raise Bad(f"{len(pro)} .kicad_pro files in {board}. Give --nickname")


def uri_of(nickname):
    return f"${{KIPRJMOD}}/lib/{nickname}.kicad_sym"


def entry(nickname):
    return (f'\t(lib (name "{nickname}")(type "KiCad")'
            f'(uri "{uri_of(nickname)}")(options "")'
            f'(descr "Symbols owned by this project"))\n')


def make_library(board, nickname):
    path = board / "lib" / f"{nickname}.kicad_sym"
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EMPTY)
    return path, True


def make_table(board, nickname):
    """The table is shared with every other library the project points at, so
    it is edited in place rather than rewritten."""
    path = board / TABLE
    if not path.exists():
        path.write_text("(sym_lib_table\n\t(version 7)\n"
                        + entry(nickname) + ")\n")
        return path, True

    src = path.read_text()
    # One entry per line, which is how KiCad writes the table. The whole line
    # is taken, because the nickname closes its own bracket long before the
    # uri that has to be checked.
    for line in src.splitlines():
        if re.search(r'\(name "%s"\)' % re.escape(nickname), line):
            uri = re.search(r'\(uri "([^"]*)"\)', line)
            if not uri or uri.group(1) != uri_of(nickname):
                raise Bad(f"{path}: '{nickname}' already points at "
                          f"{uri.group(1) if uri else 'nothing'}, not "
                          f"{uri_of(nickname)}. Nothing was changed")
            return path, False

    close = src.rstrip().rfind(")")
    if close < 0:
        raise Bad(f"{path} does not read as a sym_lib_table")
    path.write_text(src[:close] + entry(nickname) + src[close:])
    return path, True


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("--nickname", help="the library nickname. Defaults to "
                                       "the .kicad_pro name")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")

    nickname = nickname_of(board, args.nickname)
    lib, made_lib = make_library(board, nickname)
    table, made_entry = make_table(board, nickname)

    print(f"{lib}  {'created' if made_lib else 'already there'}")
    print(f"{table}  {'entry written' if made_entry else 'entry already there'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
