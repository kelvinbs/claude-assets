#!/usr/bin/env python3
"""init-pipeline — create the blank framework and the KiCad project.

The schema is T2.3 through T2.8 of board-build-tool.md and nothing else.
The script is the one place it is written down in executable form. The
KiCad side satisfies section 3.2: every path `${KIPRJMOD}`-relative, the
nickname the project's own. The KiCad files live in `design/` under the
project root (T3.1); at first init the name is the root's — the parent of
`design/` (T2.13) — and thereafter the database is master: the name is
read, never derived. Project filenames take the project name.

    python3 ${CLAUDE_PLUGIN_ROOT}/skills/init-pipeline/init-pipeline.py <board-dir> [--scorch [warm|cold]]

It adds what is missing and leaves what is there — a table, or a column
the schema names that an existing table lacks (added at the end, T2.4
`unit` was one). Any other column mismatch stops the run and is named.
Nothing is dropped or emptied, ever.
"""

import json
import re
import shutil
import sqlite3
import subprocess
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
        # T2.13 — one row, the project name. Seeded once; master thereafter
        "project_table": (
            "name          TEXT PRIMARY KEY NOT NULL",
        ),
        "parts_table": (
            "ipn           TEXT PRIMARY KEY NOT NULL",
            "description   TEXT",
            "value         TEXT",
            "symbol        TEXT",
            "footprint     TEXT",
            "source        TEXT",
            "note          TEXT",
            "name          TEXT",
            "mpn           TEXT",
            "manufacturer  TEXT",
            "datasheet     TEXT",
            "checked       TEXT NOT NULL DEFAULT 'no'",
            "pinout_checked TEXT NOT NULL DEFAULT 'no'",
        ),
        # T2.4 — an instance is a symbol on a sheet, in one instance of
        # that sheet: (uuid, path). path is '' on a root page, else the
        # chain of sub-sheet instance uuids down to the symbol's sheet
        "ref_table": (
            "uuid          TEXT NOT NULL",
            "ipn           TEXT NOT NULL REFERENCES parts_table(ipn)"
            " ON DELETE RESTRICT",
            "parent        TEXT",
            "ref           TEXT",
            "page          TEXT",
            "room          TEXT",
            "unit          INTEGER",
            "path          TEXT NOT NULL DEFAULT ''",
            "parent_path   TEXT",
            # the instance's place on its sheet, mm, y down, degrees;
            # null until pulled or placed by hand
            "x             REAL",
            "y             REAL",
            "rot           INTEGER",
            # who set the place: tool, the packer; hand, a pull found it moved
            "placed        TEXT",
            "PRIMARY KEY (uuid, path)",
            "FOREIGN KEY (parent, parent_path) REFERENCES ref_table(uuid, path)"
            " ON DELETE SET NULL",
        ),
        # T2.6 — the price survey. One row per vendor break, so a build of
        # any size reads off it and the survey is done once
        "price_table": (
            "ipn           TEXT NOT NULL REFERENCES parts_table(ipn)"
            " ON DELETE RESTRICT",
            "vendor        TEXT NOT NULL",
            "vendor_pn     TEXT NOT NULL",
            "break_qty     INTEGER NOT NULL",
            "unit_price    REAL",
            "stock         INTEGER",
            "checked       TEXT",
            "library       TEXT",
            "PRIMARY KEY (ipn, vendor, vendor_pn, break_qty)",
        ),
        # T2.6a — the nets. One row per symbol pin that carries a net
        # name; the sheet gets a label at that pin from it. Keyed by the
        # symbol, not the instance: a label is drawn once in the file.
        # No declared key to ref_table, whose key is (uuid, path) —
        # table-write removes the rows when the symbol's last row goes
        "net_table": (
            "uuid          TEXT NOT NULL",
            "pin           TEXT NOT NULL",
            "net           TEXT NOT NULL",
            "PRIMARY KEY (uuid, pin)",
        ),
        # T2.6b — the buses. A net is in at most one bus; the bus alias
        # goes to the project file and the members keep their names
        "bus_table": (
            "net           TEXT PRIMARY KEY NOT NULL",
            "bus           TEXT NOT NULL",
        ),
    },
}

class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"init-pipeline: {message}")


CONSTRAINTS = ("PRIMARY KEY", "FOREIGN KEY")


def columns_of(spec):
    """The column names a table definition declares, in order."""
    return [line.split()[0] for line in spec
            if not line.startswith(CONSTRAINTS)]


# A table whose key changed is rebuilt, rows carried over. Detected by a
# column the old shape lacks
REBUILT = {"ref_table": "path", "net_table": None}


def rebuild(con, table, spec, found):
    """Recreate a table under the current definition and carry every row
    across. New columns take their defaults."""
    keep = [c for c in columns_of(spec) if c in found]
    cols = ", ".join(keep)
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute(create_sql(f"{table}_new", spec))
    con.execute(f"INSERT INTO {table}_new ({cols}) SELECT {cols} FROM {table}")
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    for name, sql in INDEXES.get(table, ()):
        con.execute(sql)
    con.execute("PRAGMA foreign_keys = ON")


# an index a table needs to hold a rule its columns cannot
INDEXES = {
    "parts_table": (
        ("parts_one_name",
         "create unique index if not exists parts_one_name on "
         "parts_table(name) where name is not null"),
    ),
}


def create_sql(table, spec):
    body = ",\n    ".join(spec)
    return f"CREATE TABLE {table} (\n    {body}\n)"


# ------------------------------------------------------------------- run

def extra_of(found, wanted):
    return [c for c in found if c not in wanted]


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
            if table in REBUILT:
                marker = REBUILT[table]
                fks = con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
                old_shape = ((marker and marker not in found)
                             or (marker is None and fks))
                if old_shape:
                    rebuild(con, table, spec, found)
                    if table == "ref_table":
                        # every parent a rebuilt row had was on a root page
                        con.execute("UPDATE ref_table SET parent_path = '' "
                                    "WHERE parent IS NOT NULL "
                                    "AND parent_path IS NULL")
                    report.append((table, "rebuilt, rows kept"))
                    continue
            if found != wanted and not extra_of(found, wanted) \
                    and wanted[:len(found)] == found:
                for line in spec:
                    name = line.split()[0]
                    if name not in found and not line.startswith(CONSTRAINTS):
                        con.execute(f"ALTER TABLE {table} ADD COLUMN "
                                    + line.strip())
                for name, sql in INDEXES.get(table, ()):
                    con.execute(sql)
                report.append((table, "column(s) added"))
                continue
            if found != wanted and set(found) == set(wanted):
                # same columns, another order - a column added at the end
                # by an earlier tool. Rebuilt in the schema's order
                rebuild(con, table, spec, found)
                report.append((table, "rebuilt in schema order, rows kept"))
                continue
            if found != wanted:
                missing = [c for c in wanted if c not in found]
                extra = extra_of(found, wanted)
                detail = []
                if missing:
                    detail.append("missing " + ", ".join(missing))
                if extra:
                    detail.append("not in the schema: " + ", ".join(extra))
                raise Bad(f"{path.name} {table} — " + "; ".join(detail))
            report.append((table, "present"))
        con.commit()
    finally:
        con.close()
    return made_file, report


KEEP = {"warm": {"datasheets", "parts"}, "cold": {"datasheets"}}


def scorch(board, state):
    """Empty the board directory but for what the state keeps - T1 of
    init-pipeline.md. Every other file in it is made by a tool."""
    removed = 0
    for child in sorted(board.iterdir()):
        if child.name == ".gitkeep" or child.name in KEEP[state]:
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


def fp_uri_of(project):
    return f"${{KIPRJMOD}}/lib/{project}.pretty"


def fp_entry(project):
    return (f'\t(lib (name "{project}")(type "KiCad")'
            f'(uri "{fp_uri_of(project)}")(options "")'
            f'(descr "Footprints owned by this project"))\n')


def make_pretty(board, project):
    path = board / "lib" / f"{project}.pretty"
    if path.is_dir():
        return path, False
    path.mkdir(parents=True, exist_ok=True)
    (path / ".gitkeep").touch()
    return path, True


def make_fp_table(board, project):
    """fp-lib-table, the footprint twin of sym-lib-table: one entry, the
    project's, `${KIPRJMOD}/lib/<project>.pretty` (section 3.3)."""
    path = board / "fp-lib-table"
    if not path.exists():
        path.write_text("(fp_lib_table\n\t(version 7)\n"
                        + fp_entry(project) + ")\n")
        return path, True
    src = path.read_text()
    for line in src.splitlines():
        if re.search(r'\(name "%s"\)' % re.escape(project), line):
            uri = re.search(r'\(uri "([^"]*)"\)', line)
            if not uri or uri.group(1) != fp_uri_of(project):
                raise Bad(f"{path}: '{project}' already points at "
                          f"{uri.group(1) if uri else 'nothing'}")
            return path, False
    end = src.rstrip().rfind(")")
    path.write_text(src[:end] + fp_entry(project) + src[end:])
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


def ensure_converter():
    """easyeda2kicad in a venv the tool owns, tools/board-build/.venv. Made
    once; copy-kicad-part --lcsc runs it."""
    root = Path(__file__).resolve().parents[2]
    venv = root / ".venv"
    exe = venv / "bin" / "easyeda2kicad"
    if exe.exists():
        return venv, False
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                   capture_output=True)
    run = subprocess.run([str(venv / "bin" / "pip"), "install", "-q",
                          "easyeda2kicad"], capture_output=True, text=True)
    if run.returncode != 0:
        raise Bad("could not install easyeda2kicad: "
                  + (run.stderr or run.stdout).strip()[-200:])
    return venv, True


def main(argv):
    args = argv[1:]
    burn = None
    if "--scorch" in args:
        i = args.index("--scorch")
        burn = "warm"
        if i + 1 < len(args) and args[i + 1] in KEEP:
            burn = args.pop(i + 1)
        args.pop(i)
    if len(args) != 1:
        raise Bad("usage: init-pipeline.py <board-dir> [--scorch [warm|cold]]")
    board = Path(args[0])
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if burn:
        scorch(board, burn)

    for name, tables in SCHEMA.items():
        path = board / name
        made_file, report = init_file(path, tables)
        print(f"{path}  {'created' if made_file else 'present'}")
        for table, what in report:
            print(f"    {table:12s} {what}")

    # T3.1: the design folder is named `design`; T2.13: at first init the
    # name is the project root's — the parent of `design/`. The database
    # is master thereafter and the name is read, never derived.
    if board.resolve().name != "design":
        raise Bad(f"{board} is not a design folder — the KiCad files live "
                  f"in `<project>/design/` (T3.1)")
    con = sqlite3.connect(board / "board.db")
    row = con.execute("select name from project_table").fetchone()
    if row:
        project = row[0]
    else:
        project = board.resolve().parent.name
        if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
            con.close()
            raise Bad(f"'{project}' is not usable as a project name")
        con.execute("insert into project_table (name) values (?)", (project,))
        con.commit()
    con.close()
    for path, made in (make_project(board, project),
                       make_sheet(board, project),
                       make_board(board, project),
                       make_library(board, project),
                       make_pretty(board, project),
                       make_table(board, project),
                       make_fp_table(board, project)):
        print(f"{path}  {'written' if made else 'already there'}")
    venv, made = ensure_converter()
    print(f"{venv}  {'created' if made else 'present'}  easyeda2kicad")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
