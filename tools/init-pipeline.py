#!/usr/bin/env python3
"""init-pipeline — create the blank framework and the KiCad project.

The schema is T2.3 through T2.8 of board-build-tool.md and nothing else.
The script is the one place it is written down in executable form. The
KiCad side satisfies section 3.2: every path `${KIPRJMOD}`-relative, the
nickname the project's own. Project filenames take the board folder name.

    python3 tools/board-build/tools/init-pipeline.py <board-dir> [--scorch]

It adds what is missing and leaves what is there. An existing table whose
columns do not match the schema stops the run and is named. Nothing is
dropped, altered or emptied, ever.
"""

import json
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

SCH_VERSION = 20250114
PCB_VERSION = 20241229
SYM_LIB_VERSION = 20251024

EMPTY_LIB = (
    "(kicad_symbol_lib\n"
    f"\t(version {SYM_LIB_VERSION})\n"
    '\t(generator "init-pipeline.py")\n'
    '\t(generator_version "10.0")\n'
    ")\n"
)

# ------------------------------------------------------------------ schema

SCHEMA = {
    # one file. A foreign key cannot cross two, and the split enforced
    # nothing. Order matters — a table is created after the one it references
    "board.db": {
        "parts_table": (
            "ipn           TEXT PRIMARY KEY NOT NULL",
            "description   TEXT",
            "symbol        TEXT",
            "footprint     TEXT",
            "source        TEXT",
            "note          TEXT",
        ),
        "ref_table": (
            "uuid          TEXT PRIMARY KEY NOT NULL",
            "ipn           TEXT NOT NULL REFERENCES parts_table(ipn)"
            " ON DELETE RESTRICT",
            "parent        TEXT REFERENCES ref_table(uuid) ON DELETE SET NULL",
            "ref           TEXT",
            "page          TEXT",
            "room          TEXT",
        ),
        # the manufacturer part itself. Kept, and never fetched away
        "mpn_table": (
            "mpn           TEXT PRIMARY KEY NOT NULL",
            "manufacturer  TEXT",
            "datasheet     TEXT",
        ),
        "aml_table": (
            "ipn           TEXT NOT NULL REFERENCES parts_table(ipn)"
            " ON DELETE RESTRICT",
            "mpn           TEXT NOT NULL REFERENCES mpn_table(mpn)"
            " ON DELETE RESTRICT",
            "rank          INTEGER",
            "note          TEXT",
            "PRIMARY KEY (ipn, mpn)",
        ),
        # what a fetch found. Discarded and fetched again
        "offer_table": (
            "mpn           TEXT NOT NULL REFERENCES mpn_table(mpn)"
            " ON DELETE CASCADE",
            "distributor   TEXT NOT NULL",
            "break_qty     INTEGER NOT NULL",
            "sku           TEXT",
            "currency      TEXT",
            "price         REAL",
            "stock         INTEGER",
            "moq           INTEGER",
            "lead_days     INTEGER",
            "fetched_at    TEXT",
            "PRIMARY KEY (mpn, distributor, break_qty)",
        ),
    },
}

class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"init-pipeline: {message}")


def columns_of(spec):
    """The column names a table definition declares, in order."""
    return [line.split()[0] for line in spec if not line.startswith("PRIMARY KEY")]


# an index a table needs to hold a rule its columns cannot
INDEXES = {
    "aml_table": (
        ("aml_one_default",
         "create unique index aml_one_default on aml_table(ipn)"
         " where rank is null"),
    ),
}


def create_sql(table, spec):
    body = ",\n    ".join(spec)
    return f"CREATE TABLE {table} (\n    {body}\n)"


# ------------------------------------------------------------------- run

def init_file(path, tables):
    """Create the file and any missing table. Report what happened per table."""
    made_file = not path.exists()
    report = []
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        have = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, spec in tables.items():
            if table not in have:
                con.execute(create_sql(table, spec))
                for name, sql in INDEXES.get(table, ()):
                    con.execute(sql)
                report.append((table, "created"))
                continue
            found = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            wanted = columns_of(spec)
            if found != wanted:
                missing = [c for c in wanted if c not in found]
                extra = [c for c in found if c not in wanted]
                detail = []
                if missing:
                    detail.append("missing " + ", ".join(missing))
                if extra:
                    detail.append("not in the schema: " + ", ".join(extra))
                if not detail:
                    detail.append("column order differs")
                raise Bad(f"{path.name} {table} — " + "; ".join(detail))
            report.append((table, "present"))
        con.commit()
    finally:
        con.close()
    return made_file, report


def scorch(board):
    """Empty the board directory. Every file in it is made by a tool."""
    removed = 0
    for child in sorted(board.iterdir()):
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    print(f"{board}  scorched, {removed} removed")


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
        '\t(generator "init-pipeline.py")\n'
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


EMPTY_PCB_LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
"""


def make_board(board, project):
    path = board / f"{project}.kicad_pcb"
    if path.exists():
        return path, False
    path.write_text(
        "(kicad_pcb\n"
        f"\t(version {PCB_VERSION})\n"
        '\t(generator "init-pipeline.py")\n'
        '\t(generator_version "10.0")\n'
        "\t(general\n"
        "\t\t(thickness 1.6)\n"
        "\t\t(legacy_teardrops no)\n"
        "\t)\n"
        '\t(paper "A4")\n'
        + EMPTY_PCB_LAYERS +
        "\t(embedded_fonts no)\n"
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
    args = argv[1:]
    burn = "--scorch" in args
    args = [a for a in args if a != "--scorch"]
    if len(args) != 1:
        raise Bad("usage: init-pipeline.py <board-dir> [--scorch]")
    board = Path(args[0])
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if burn:
        scorch(board)

    for name, tables in SCHEMA.items():
        path = board / name
        made_file, report = init_file(path, tables)
        print(f"{path}  {'created' if made_file else 'present'}")
        for table, what in report:
            print(f"    {table:12s} {what}")

    # re-entry, section 4.3: a project already named keeps its name.
    # Otherwise the board folder names it.
    have = sorted(board.glob("*.kicad_pro"))
    project = have[0].stem if have else board.resolve().name
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        raise Bad(f"'{project}' is not usable as a project name")
    for path, made in (make_project(board, project),
                       make_sheet(board, project),
                       make_board(board, project),
                       make_library(board, project),
                       make_table(board, project)):
        print(f"{path}  {'written' if made else 'already there'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
