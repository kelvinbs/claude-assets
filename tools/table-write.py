#!/usr/bin/env python3
"""table-write — create or modify a part.

The tool of process 1. It writes `parts_table` and the `ref_table` rows that
go with a part, and nothing else. It never reads `sourcing.db` and never
touches a KiCad file.

    table-write.py <board-dir> add   --class A --description "..." [options]
    table-write.py <board-dir> set   <ipn> [--field value ...]
    table-write.py <board-dir> place <ipn> --count N [--page P] [--room R]
    table-write.py <board-dir> drop  <ref>
    table-write.py <board-dir> show  [<ipn>]

It adds what is missing and leaves what is there. An instance is removed only
by naming its reference, one at a time.
"""

import argparse
import re
import sqlite3
import sys
import uuid
from pathlib import Path

# T1.2 — the part classes, and the reference-designator prefix each
# annotates under
CLASSES = {
    "A": ("amplifier", "U"),
    "C": ("capacitor", "C"),
    "E": ("antenna", "AE"),
    "F": ("filter", "FL"),
    "G": ("synthesizer", "U"),
    "H": ("mechanical", "H"),
    "J": ("connector", "J"),
    "K": ("switch", "U"),
    "L": ("inductor", "L"),
    "M": ("mixer", "U"),
    "P": ("power", "U"),
    "R": ("resistor", "R"),
    "S": ("sensor", "U"),
    "T": ("test point", "TP"),
    "U": ("processor", "U"),
    "W": ("splitter", "U"),
    "Y": ("oscillator", "Y"),
}

STATUS = ("chosen", "drawn", "checked", "provisional", "blocked")
SOURCE = ("stock", "vendor", "hand")

IPN = re.compile(r"^([A-Z])(\d{4})$")
REF = re.compile(r"^([A-Z]+)(\d+)$")

FIELDS = ("description", "category", "symbol", "footprint", "model",
          "pins_checked", "source", "status")


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"table-write: {message}")


def connect(board):
    path = Path(board) / "design.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    con = sqlite3.connect(path)
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table", "ref_table"} <= have:
        raise Bad(f"{path} is missing a table. Run db-init")
    return con


def next_ipn(con, letter):
    used = [IPN.match(r[0]) for r in
            con.execute("select ipn from parts_table where ipn glob ?",
                        (f"{letter}[0-9][0-9][0-9][0-9]",))]
    n = max((int(m.group(2)) for m in used if m), default=0) + 1
    if n > 9999:
        raise Bad(f"class {letter} is full")
    return f"{letter}{n:04d}"


def next_ref(con, prefix):
    """The lowest free number for a prefix. Numbers are never reused while
    the reference they belong to is still in the table."""
    used = set()
    for (ref,) in con.execute("select ref from ref_table where ref is not null"):
        m = REF.match(ref)
        if m and m.group(1) == prefix:
            used.add(int(m.group(2)))
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n}"


def part(con, ipn):
    row = con.execute(
        f"select ipn, {', '.join(FIELDS)} from parts_table where ipn = ?",
        (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    return dict(zip(("ipn",) + FIELDS, row))


def check(field, value):
    if value is None:
        return None
    if field == "status" and value not in STATUS:
        raise Bad(f"status must be one of {', '.join(STATUS)}, found '{value}'")
    if field == "source" and value not in SOURCE:
        raise Bad(f"source must be one of {', '.join(SOURCE)}, found '{value}'")
    return value


# ------------------------------------------------------------------ verbs

def add(con, args):
    letter = args.cls.upper()
    if letter not in CLASSES:
        raise Bad(f"class must be one of {' '.join(sorted(CLASSES))}, "
                  f"found '{args.cls}'")
    if not args.description:
        raise Bad("a part needs a description")

    category, prefix = CLASSES[letter]
    ipn = next_ipn(con, letter)
    values = {f: check(f, getattr(args, f, None)) for f in FIELDS}
    values["description"] = args.description
    values["category"] = category
    values["status"] = values["status"] or "chosen"

    con.execute(
        f"insert into parts_table (ipn, {', '.join(FIELDS)}) "
        f"values (?, {', '.join('?' * len(FIELDS))})",
        (ipn,) + tuple(values[f] for f in FIELDS))
    print(f"{ipn}  {category}  {values['status']}")

    for _ in range(args.count):
        ref = next_ref(con, prefix)
        con.execute("insert into ref_table values (?,?,?,?,?)",
                    (str(uuid.uuid4()), ipn, ref, args.page, args.room))
        print(f"    {ref}  {args.page or '—'}")
    con.commit()


def setf(con, args):
    row = part(con, args.ipn)
    changes = {}
    for f in FIELDS:
        v = getattr(args, f, None)
        if v is not None:
            changes[f] = check(f, v)
    if not changes:
        raise Bad("no field given")
    if "category" in changes:
        raise Bad("category follows the IPN letter and is not set by hand")
    con.execute(f"update parts_table set {', '.join(f'{f} = ?' for f in changes)}"
                f" where ipn = ?", tuple(changes.values()) + (args.ipn,))
    con.commit()
    for f, v in changes.items():
        print(f"{args.ipn}  {f}: {row[f] or '—'} -> {v}")


def place(con, args):
    part(con, args.ipn)
    prefix = CLASSES[args.ipn[0]][1]
    have = con.execute("select count(*) from ref_table where ipn = ?",
                       (args.ipn,)).fetchone()[0]
    if args.count < have:
        raise Bad(f"{args.ipn} has {have} instances. This tool does not "
                  f"remove them — name the reference with drop")
    for _ in range(args.count - have):
        ref = next_ref(con, prefix)
        con.execute("insert into ref_table values (?,?,?,?,?)",
                    (str(uuid.uuid4()), args.ipn, ref, args.page, args.room))
        print(f"{args.ipn}  {ref}  {args.page or '—'}")
    if args.page or args.room:
        sets, vals = [], []
        if args.page:
            sets.append("page = ?"); vals.append(args.page)
        if args.room:
            sets.append("room = ?"); vals.append(args.room)
        con.execute(f"update ref_table set {', '.join(sets)} where ipn = ?",
                    tuple(vals) + (args.ipn,))
    con.commit()
    print(f"{args.ipn}  {max(args.count, have)} instances")


def drop(con, args):
    row = con.execute("select ipn from ref_table where ref = ?",
                      (args.ref,)).fetchone()
    if row is None:
        raise Bad(f"{args.ref} is not in ref_table")
    con.execute("delete from ref_table where ref = ?", (args.ref,))
    con.commit()
    print(f"{args.ref}  removed from {row[0]}")


def show(con, args):
    where, vals = ("where ipn = ?", (args.ipn,)) if args.ipn else ("", ())
    rows = con.execute(
        f"select ipn, description, category, status, source, symbol, footprint"
        f" from parts_table {where} order by ipn", vals).fetchall()
    if not rows:
        raise Bad("nothing to show")
    for ipn, desc, cat, status, source, sym, fp in rows:
        refs = [r[0] for r in con.execute(
            "select ref from ref_table where ipn = ? order by ref", (ipn,))]
        print(f"{ipn}  {cat:12s} {status or '—':12s} {(desc or '')[:44]}")
        print(f"    refs: {' '.join(refs) or '—'}")
        if args.ipn:
            print(f"    source: {source or '—'}  symbol: {sym or '—'}"
                  f"  footprint: {fp or '—'}")


# ------------------------------------------------------------------- entry

def main(argv):
    ap = argparse.ArgumentParser(prog="table-write.py", add_help=True)
    ap.add_argument("board", help="the KiCad project directory")
    sub = ap.add_subparsers(dest="verb", required=True)

    def fields(p):
        for f in FIELDS:
            if f != "category":
                p.add_argument(f"--{f.replace('_', '-')}", dest=f)

    a = sub.add_parser("add", help="create a part")
    a.add_argument("--class", dest="cls", required=True)
    a.add_argument("--count", type=int, default=1,
                   help="instances to create. Default 1")
    a.add_argument("--page")
    a.add_argument("--room")
    fields(a)
    a.set_defaults(run=add)

    s = sub.add_parser("set", help="change a field")
    s.add_argument("ipn")
    fields(s)
    s.set_defaults(run=setf)

    p = sub.add_parser("place", help="raise the instance count")
    p.add_argument("ipn")
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--page")
    p.add_argument("--room")
    p.set_defaults(run=place)

    d = sub.add_parser("drop", help="remove one instance")
    d.add_argument("ref")
    d.set_defaults(run=drop)

    w = sub.add_parser("show", help="print parts and their instances")
    w.add_argument("ipn", nargs="?")
    w.set_defaults(run=show)

    args = ap.parse_args(argv[1:])
    if args.verb in ("place", "set") and not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")
    con = connect(args.board)
    try:
        args.run(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
