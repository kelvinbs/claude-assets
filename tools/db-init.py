#!/usr/bin/env python3
"""db-init — create the database and its tables.

The schema is T1.2 and T1.3 of board-build-tool.md and nothing else. The
script is the one place it is written down in executable form.

    python3 tools/board-build/tools/db-init.py <board-dir>

It adds what is missing and leaves what is there. An existing table whose
columns do not match the schema stops the run and is named. Nothing is
dropped, altered or emptied, ever.
"""

import sqlite3
import sys
from pathlib import Path

# ------------------------------------------------------------------ schema

SCHEMA = {
    # one file. A foreign key cannot cross two, and the split enforced
    # nothing. Order matters — a table is created after the one it references
    "board.db": {
        "parts_table": (
            "ipn           TEXT PRIMARY KEY",
            "description   TEXT",
            "category      TEXT",
            "parent        TEXT REFERENCES parts_table(ipn) ON DELETE SET NULL",
            "symbol        TEXT",
            "footprint     TEXT",
            "model         TEXT",
            "source        TEXT",
            "note          TEXT",
        ),
        "ref_table": (
            "uuid          TEXT PRIMARY KEY",
            "ipn           TEXT NOT NULL REFERENCES parts_table(ipn)"
            " ON DELETE RESTRICT",
            "ref           TEXT",
            "page          TEXT",
            "room          TEXT",
            "note          TEXT",
        ),
        # the manufacturer part itself. Kept, and never fetched away
        "mpn_table": (
            "mpn           TEXT PRIMARY KEY",
            "manufacturer  TEXT",
            "package       TEXT",
            "pin_count     INTEGER",
            "pitch_mm      REAL",
            "datasheet     TEXT",
            "note          TEXT",
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
        "lifecycle_table": (
            "mpn           TEXT PRIMARY KEY REFERENCES mpn_table(mpn)"
            " ON DELETE CASCADE",
            "lifecycle     TEXT",
            "fetched_at    TEXT",
        ),
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

# an index a table needs to hold a rule its columns cannot
INDEXES = {
    "aml_table": (
        "create unique index aml_one_default on aml_table(ipn)"
        " where rank is null",
    ),
}


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"db-init: {message}")


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


def main(argv):
    if len(argv) != 2:
        raise Bad("usage: db-init.py <board-dir>")
    board = Path(argv[1])
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")

    for name, tables in SCHEMA.items():
        path = board / name
        made_file, report = init_file(path, tables)
        print(f"{path}  {'created' if made_file else 'present'}")
        for table, what in report:
            print(f"    {table:12s} {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
