#!/usr/bin/env python3
"""table-read — show the record, one view per workflow step.

    table-read.py <board-dir> [view] [--page P ...] [--out FILE]

The views and their SQL are table 3 of `table-read.md`. This script holds
them in executable form and is the only place they are written as SQL. A
view added to the document is added here in the same commit.

It writes and changes nothing. With no view named it prints every one.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

VIEWS = {
    "parts_view": (
        "What is on the board",
        "select p.ipn, p.description, p.parent, count(r.uuid) as count, "
        "group_concat(distinct r.page) as pages, "
        "(select a.mpn from aml_table a where a.ipn = p.ipn order by a.rank) "
        "as mpn from parts_table p left join ref_table r using (ipn) "
        "group by p.ipn order by p.ipn"),
    "assembly_view": (
        "What a function is built from",
        "select coalesce(p.parent, p.ipn) as parent, p.ipn, p.description, "
        "count(r.uuid) as count from parts_table p "
        "left join ref_table r using (ipn) group by p.ipn "
        "order by parent, p.parent is null desc, p.ipn"),
    "page_view": (
        "What goes on a sheet",
        "select r.page, r.ref, r.ipn, p.description from ref_table r "
        "join parts_table p using (ipn) order by r.page, r.ref"),
    "library_view": (
        "What is drawn and what is not",
        "select ipn, description, symbol, footprint, source from parts_table "
        "order by symbol is not null, footprint is not null, ipn"),
    "sourcing_view": (
        "What is bought",
        "select a.ipn, p.description, a.mpn, a.rank, m.manufacturer, "
        "m.datasheet from aml_table a join parts_table p on p.ipn = a.ipn "
        "join mpn_table m on m.mpn = a.mpn order by a.ipn, a.rank"),
    "ready_view": (
        "What blocks layout",
        "select p.ipn, p.description, trim("
        "case when not exists (select 1 from aml_table a where a.ipn = p.ipn) "
        "then 'mpn ' else '' end || "
        "case when p.symbol is null then 'symbol ' else '' end || "
        "case when p.footprint is null then 'footprint' else '' end"
        ") as missing from parts_table p where missing <> '' order by p.ipn"),
}

WIDTH = 48   # a description is a sentence; the view is a table


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"table-read: {message}")


def cell(value):
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= WIDTH else text[:WIDTH - 1] + "…"


def markdown(names, rows):
    out = ["| " + " | ".join(names) + " |",
           "|" + "|".join("---" for _ in names) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(out)


def run(con, name, pages):
    label, sql = VIEWS[name]
    if pages:
        if "r.page" not in sql:
            raise Bad(f"{name} has no page to filter on")
        holes = ",".join("?" * len(pages))
        sql = sql.replace(" order by", f" where r.page in ({holes}) order by")
    cur = con.execute(sql, tuple(pages) if pages else ())
    names = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return f"## {name} — {label}\n\n{markdown(names, rows)}\n"


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("view", nargs="?", help="one of: " + " ".join(VIEWS))
    ap.add_argument("--page", action="append", default=[],
                    help="restrict page_view to this page. Repeatable")
    ap.add_argument("--out", help="write here instead of standard output")
    args = ap.parse_args(argv[1:])

    path = Path(args.board) / "board.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    if args.view and args.view not in VIEWS:
        raise Bad(f"'{args.view}' is not a view. One of: " + " ".join(VIEWS))

    con = sqlite3.connect(path)
    try:
        wanted = [args.view] if args.view else list(VIEWS)
        text = "\n".join(run(con, n, args.page if n == "page_view" else [])
                         for n in wanted)
    finally:
        con.close()

    if args.out:
        Path(args.out).write_text(text)
        print(f"{args.out}  written")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
