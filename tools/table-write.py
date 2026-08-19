#!/usr/bin/env python3
"""table-write — create or modify a part.

The tool of process 1. It writes `parts_table`, the `ref_table` rows that go
with a part, and the `aml_table` approval that says which manufacturer part
may be built against it. It never touches a KiCad file.

    table-write.py <board-dir> add   --class A --description "..." [options]
    table-write.py <board-dir> set   <ipn> [--field value ...]
    table-write.py <board-dir> place <ipn> --count N [--page P] [--room R]
    table-write.py <board-dir> mpn   <ipn> <mpn> [--rank N] [--note ...]
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

SOURCE = re.compile(r"^[svh-]/[svh-]$")   # symbol/footprint — T1.2

IPN = re.compile(r"^([A-Z])(\d{4})$")
REF = re.compile(r"^([A-Z]+)(\d+)$")

FIELDS = ("description", "category", "parent", "symbol", "footprint",
          "model", "source", "note")


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"table-write: {message}")


def connect(board, name="board.db",
            need=("parts_table", "ref_table", "aml_table", "mpn_table")):
    path = Path(board) / name
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not set(need) <= have:
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


def has_mpn(con, ipn):
    return con.execute("select 1 from aml_table where ipn = ?",
                       (ipn,)).fetchone() is not None


def guard_footprint(con, ipn, footprint):
    """A footprint is a land pattern. A land pattern is a package, and a
    package is a manufacturer part. Until one is named against the IPN there
    is nothing for a footprint to be."""
    if footprint and not has_mpn(con, ipn):
        raise Bad(f"{ipn} has no MPN in aml_table. A footprint is a package, "
                  f"and a package needs a part number — name one with mpn "
                  f"first")


def guard_parent(con, ipn, parent):
    if parent is None:
        return
    if not IPN.match(parent):
        raise Bad(f"'{parent}' is not an IPN")
    if parent == ipn:
        raise Bad(f"{ipn} cannot be its own parent")
    if con.execute("select 1 from parts_table where ipn = ?",
                   (parent,)).fetchone() is None:
        raise Bad(f"parent {parent} is not in parts_table")


def check(field, value):
    if value is None:
        return None
    if field == "source" and not SOURCE.match(value):
        raise Bad(f"source is two letters, symbol then footprint, from "
                  f"s v h or -, as in 's/h'. Found '{value}'")
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

    guard_parent(con, ipn, values["parent"])
    guard_footprint(con, ipn, values["footprint"])

    con.execute(
        f"insert into parts_table (ipn, {', '.join(FIELDS)}) "
        f"values (?, {', '.join('?' * len(FIELDS))})",
        (ipn,) + tuple(values[f] for f in FIELDS))
    print(f"{ipn}  {category}")

    for _ in range(args.count):
        ref = next_ref(con, prefix)
        con.execute("insert into ref_table values (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), ipn, ref, args.page, args.room, None))
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
    guard_parent(con, args.ipn, changes.get("parent"))
    guard_footprint(con, args.ipn, changes.get("footprint"))
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
        con.execute("insert into ref_table values (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), args.ipn, ref, args.page, args.room, None))
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


def mpn(con, args):
    """An approval: this manufacturer part may be built against this IPN.
    It is kept, unlike the fetched tables beside it."""
    part(con, args.ipn)
    # the part number's identity exists from the moment it is named, with
    # every field but the key empty until something fills them in
    con.execute("insert or ignore into mpn_table (mpn) values (?)",
                (args.mpn,))
    have = con.execute(
        "select rank from aml_table where ipn = ? and mpn = ?",
        (args.ipn, args.mpn)).fetchone()
    shown = args.rank if args.rank is not None else "default"
    try:
        if have:
            con.execute("update aml_table set rank = ?, note = ?"
                        " where ipn = ? and mpn = ?",
                        (args.rank, args.note, args.ipn, args.mpn))
            was = have[0] if have[0] is not None else "default"
            print(f"{args.ipn}  {args.mpn}  rank {was} -> {shown}")
        else:
            con.execute("insert into aml_table values (?,?,?,?)",
                        (args.ipn, args.mpn, args.rank, args.note))
            print(f"{args.ipn}  {args.mpn}  rank {shown}")
    except sqlite3.IntegrityError:
        other = con.execute("select mpn from aml_table where ipn = ?"
                            " and rank is null", (args.ipn,)).fetchone()
        raise Bad(f"{args.ipn} already has a default — {other[0]}. Rank "
                  f"this one, or rank that one first")
    con.commit()


def show(con, args):
    where, vals = ("where ipn = ?", (args.ipn,)) if args.ipn else ("", ())
    rows = con.execute(
        f"select ipn, description, category, parent, source, symbol, footprint"
        f" from parts_table {where} order by ipn", vals).fetchall()
    if not rows:
        raise Bad("nothing to show")
    for ipn, desc, cat, parent, source, sym, fp in rows:
        refs = [r[0] for r in con.execute(
            "select ref from ref_table where ipn = ? order by ref", (ipn,))]
        print(f"{ipn}  {cat:12s} {(desc or '')[:52]}"
              + (f"   parent {parent}" if parent else ""))
        print(f"    refs: {' '.join(refs) or '—'}")
        if args.ipn:
            print(f"    source: {source or '—'}  symbol: {sym or '—'}"
                  f"  footprint: {fp or '—'}")
            kids = [r[0] for r in con.execute(
                "select ipn from parts_table where parent = ? order by ipn",
                (ipn,))]
            if kids:
                print(f"    children: {' '.join(kids)}")
            for m, rank in con.execute(
                    "select mpn, rank from aml_table"
                    " where ipn = ? order by rank", (ipn,)):
                print(f"    mpn: {m}  rank "
                      f"{rank if rank is not None else 'default'}")


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

    m = sub.add_parser("mpn", help="approve a manufacturer part against an IPN")
    m.add_argument("ipn")
    m.add_argument("mpn")
    m.add_argument("--rank", type=int,
                   help="blank on the one you designed against")
    m.add_argument("--note")
    m.set_defaults(run=mpn)

    d = sub.add_parser("drop", help="remove one instance")
    d.add_argument("ref")
    d.set_defaults(run=drop)

    w = sub.add_parser("show", help="print parts and their instances")
    w.add_argument("ipn", nargs="?")
    w.set_defaults(run=show)

    args = ap.parse_args(argv[1:])
    if args.verb in ("place", "set", "mpn") and not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")
    con = connect(args.board)
    try:
        args.run(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
