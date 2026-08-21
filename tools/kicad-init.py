#!/usr/bin/env python3
"""kicad-init - create the KiCad project from nothing.

    kicad-init.py <board-dir> --project NAME

Step 3 of the Init stage — T4.2. It makes the project file, the root
sheet, the empty symbol library, and the `sym-lib-table` entry that
resolves it, so Update library has somewhere to put a symbol.

Section 3.2 is what it satisfies: the table sits in the project directory,
every path in it is `${KIPRJMOD}`-relative, and the nickname is the
project's, so a fresh clone opens with nothing missing and a global entry on
another machine cannot collide.

It adds what is missing and leaves what is there.
"""

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

SCH_VERSION = 20250114

SYM_LIB_VERSION = 20251024

EMPTY_LIB = (
    "(kicad_symbol_lib\n"
    f"\t(version {SYM_LIB_VERSION})\n"
    '\t(generator "kicad-init.py")\n'
    '\t(generator_version "10.0")\n'
    ")\n"
)


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"kicad-init: {message}")


def uri_of(project):
    return f"${{KIPRJMOD}}/lib/{project}.kicad_sym"


def entry(project):
    return (f'\t(lib (name "{project}")(type "KiCad")'
            f'(uri "{uri_of(project)}")(options "")'
            f'(descr "Symbols owned by this project"))\n')


def make_project(board, project):
    path = board / f"{project}.kicad_pro"
    if path.exists():
        return path, False
    # KiCad's own template, which is what it writes for an empty project.
    path.write_text(json.dumps({
        "board": {"design_settings": {"defaults": {},
                                      "diff_pair_dimensions": [],
                                      "drc_exclusions": [], "rules": {},
                                      "track_widths": [],
                                      "via_dimensions": []}},
        "boards": [],
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{project}.kicad_pro", "version": 1},
        "net_settings": {"classes": [], "meta": {"version": 0}},
        "pcbnew": {"page_layout_descr_file": ""},
        "sheets": [],
        "text_variables": {},
    }, indent=2) + "\n")
    return path, True


def make_sheet(board, project):
    path = board / f"{project}.kicad_sch"
    if path.exists():
        return path, False
    path.write_text(
        "(kicad_sch\n"
        f"\t(version {SCH_VERSION})\n"
        '\t(generator "kicad-init.py")\n'
        '\t(generator_version "10.0")\n'
        f'\t(uuid "{uuid.uuid4()}")\n'
        '\t(paper "A4")\n'
        "\t(lib_symbols)\n"
        "\t(sheet_instances\n"
        '\t\t(path "/"\n'
        '\t\t\t(page "1")\n'
        "\t\t)\n"
        "\t)\n"
        ")\n")
    return path, True


def make_library(board, project):
    path = board / "lib" / f"{project}.kicad_sym"
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EMPTY_LIB)
    return path, True


def make_table(board, project):
    """The table is shared with every other library the project points at, so
    it is edited in place rather than rewritten."""
    path = board / "sym-lib-table"
    if not path.exists():
        path.write_text("(sym_lib_table\n\t(version 7)\n"
                        + entry(project) + ")\n")
        return path, True

    src = path.read_text()
    # One entry per line, which is how KiCad writes the table. The whole line
    # is taken, because the nickname closes its own bracket long before the
    # uri that has to be checked.
    for line in src.splitlines():
        if re.search(r'\(name "%s"\)' % re.escape(project), line):
            uri = re.search(r'\(uri "([^"]*)"\)', line)
            if not uri or uri.group(1) != uri_of(project):
                raise Bad(f"{path}: '{project}' already points at "
                          f"{uri.group(1) if uri else 'nothing'}, not "
                          f"{uri_of(project)}. Nothing was changed")
            return path, False

    close = src.rstrip().rfind(")")
    if close < 0:
        raise Bad(f"{path} does not read as a sym_lib_table")
    path.write_text(src[:close] + entry(project) + src[close:])
    return path, True


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("--project", required=True,
                    help="the project name. It names the files and the "
                         "library nickname")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if not (board / "board.db").exists():
        raise Bad(f"{board} has no board.db — run init-pipeline first")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.project):
        raise Bad(f"'{args.project}' is not usable as a project name")

    for path, made in (make_project(board, args.project),
                       make_sheet(board, args.project),
                       make_library(board, args.project),
                       make_table(board, args.project)):
        print(f"{path}  {'written' if made else 'already there'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
