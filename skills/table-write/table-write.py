#!/usr/bin/env python3
"""table-write — create or modify a part.

The skill of Update parts. It writes the part, its instances, and the
approval that says which manufacturer part may be built against it. The
library fields of `parts_table` belong to Update library, which copies each
object into `lib/` before naming it. Parenthood is a property of use:
`parent` sits on `ref_table` and names the parent instance — T2.4, T2.9.

    table-write.py <board-dir> add    --class A --description "..." [options]
    table-write.py <board-dir> set    <ipn> [--field value ...]
    table-write.py <board-dir> place  <ipn> --count N [--page P] [--room R]
    table-write.py <board-dir> parent <ref> --under <ref> | --none
    table-write.py <board-dir> drop   <ref>
    table-write.py <board-dir> price  <ipn> --vendor V --break QTY:PRICE ...
    table-write.py <board-dir> net    <ref> <pin> <name> | --none
    table-write.py <board-dir> show   [<ipn>]

It adds what is missing and leaves what is there. An instance is removed only
by naming its reference, one at a time.
"""

import argparse
import re
import sqlite3
import sys
import uuid
from pathlib import Path

# T2.10 — the part classes, and the reference-designator prefix each
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

IPN = re.compile(r"^([A-Z])(\d{4})$")
REF = re.compile(r"^([A-Z]+)(\d+)$")

# Update parts writes the part. The library objects are Update library's,
# written by symbol-draw and footprint-draw once copied into lib/
FIELDS = ("description", "value", "note", "name", "mpn", "manufacturer",
          "datasheet", "footprint")


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"table-write: {message}")


def connect(board, name="board.db",
            need=("parts_table", "ref_table")):
    path = Path(board) / name
    if not path.exists():
        raise Bad(f"{path} does not exist. Run init-pipeline first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not set(need) <= have:
        raise Bad(f"{path} is missing a table. Run init-pipeline")
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


def resolve(con, token):
    """A part named any way a person would: the name, else an approved
    MPN, else the IPN itself (n0.3)."""
    row = con.execute("select ipn from parts_table where name = ?",
                      (token,)).fetchone()
    if row:
        return row[0]
    row = con.execute("select ipn from parts_table where mpn = ?",
                      (token,)).fetchone()
    if row:
        return row[0]
    return token


def part(con, ipn):
    row = con.execute(
        f"select ipn, {', '.join(FIELDS)} from parts_table where ipn = ?",
        (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    return dict(zip(("ipn",) + FIELDS, row))


def parent_uuid(con, ref):
    """Resolve a reference to its instance uuid."""
    row = con.execute("select uuid from ref_table where ref = ?",
                      (ref,)).fetchone()
    if row is None:
        raise Bad(f"parent {ref} names no instance")
    return row[0]


def check(field, value):
    if value is None:
        return None
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
    under = parent_uuid(con, args.parent) if args.parent else None

    con.execute(
        f"insert into parts_table (ipn, {', '.join(FIELDS)}) "
        f"values (?, {', '.join('?' * len(FIELDS))})",
        (ipn,) + tuple(values[f] for f in FIELDS))
    print(f"{ipn}  {category}")

    for _ in range(args.count):
        ref = next_ref(con, prefix)
        con.execute("insert into ref_table (uuid, ipn, parent, ref, "
                    "page, room) values (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), ipn, under, ref, args.page,
                     args.room))
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
    under = parent_uuid(con, args.parent) if args.parent else None
    for _ in range(args.count - have):
        ref = next_ref(con, prefix)
        con.execute("insert into ref_table (uuid, ipn, parent, ref, "
                    "page, room) values (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), args.ipn, under, ref, args.page,
                     args.room))
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


def reparent(con, args):
    row = con.execute("select uuid, parent from ref_table where ref = ?",
                      (args.ref,)).fetchone()
    if row is None:
        raise Bad(f"{args.ref} is not in ref_table")
    child, was = row
    if args.none:
        new = None
    else:
        if not args.under:
            raise Bad("give --under <ref> or --none")
        new = parent_uuid(con, args.under)
        walk = new
        while walk is not None:
            if walk == child:
                raise Bad(f"{args.ref} under {args.under} closes a loop")
            walk = con.execute("select parent from ref_table where uuid = ?",
                               (walk,)).fetchone()[0]
    con.execute("update ref_table set parent = ? where uuid = ?",
                (new, child))
    con.commit()
    print(f"{args.ref}  parent {ref_of(con, was) or '—'} -> "
          f"{args.under if new else '—'}")


def ref_of(con, u):
    if u is None:
        return None
    row = con.execute("select ref from ref_table where uuid = ?",
                      (u,)).fetchone()
    return row[0] if row else None


def drop(con, args):
    row = con.execute("select ipn from ref_table where ref = ?",
                      (args.ref,)).fetchone()
    if row is None:
        raise Bad(f"{args.ref} is not in ref_table")
    con.execute("delete from ref_table where ref = ?", (args.ref,))
    con.commit()
    print(f"{args.ref}  removed from {row[0]}")
def price(con, args):
    """Record a vendor's price survey: one row per break. Upsert, so a
    re-survey overwrites the tier it re-quotes and leaves the rest."""
    part(con, args.ipn)
    breaks = []
    for token in args.brk:
        if ":" not in token:
            raise Bad(f"--break wants QTY:PRICE, got '{token}'")
        q, _, v = token.partition(":")
        try:
            breaks.append((int(q.replace(",", "")), float(v)))
        except ValueError:
            raise Bad(f"--break wants QTY:PRICE, got '{token}'")
    if not breaks:
        raise Bad("no --break given")
    for qty, unit in sorted(breaks):
        con.execute(
            "insert into price_table (ipn, vendor, vendor_pn,"
            " break_qty, unit_price, stock, checked) values (?,?,?,?,?,?,?)"
            " on conflict(ipn, vendor, vendor_pn, break_qty) do update set"
            " unit_price = excluded.unit_price,"
            " stock = excluded.stock,"
            " checked = excluded.checked",
            (args.ipn, args.vendor, args.vendor_pn, qty, unit,
             args.stock, args.checked))
    con.commit()
    print(f"{args.ipn}  {args.vendor} {args.vendor_pn}:"
          f" {len(breaks)} breaks recorded")


def net(con, args):
    """Name the net on one pin of one instance, or clear it. Upsert on
    (uuid, pin). The sheet takes a global label at that pin on the next
    place or push."""
    u = parent_uuid(con, args.ref)
    if args.none:
        n = con.execute("delete from net_table where uuid = ? and pin = ?",
                        (u, args.pin)).rowcount
        con.commit()
        print(f"{args.ref}  pin {args.pin}: net cleared" if n
              else f"{args.ref}  pin {args.pin}: no net to clear")
        return
    if not args.name:
        raise Bad("net wants a name, or --none")
    con.execute(
        "insert into net_table (uuid, pin, net) values (?,?,?)"
        " on conflict(uuid, pin) do update set net = excluded.net",
        (u, args.pin, args.name))
    con.commit()
    print(f"{args.ref}  pin {args.pin}: {args.name}")


def show(con, args):
    where, vals = ("where ipn = ?", (args.ipn,)) if args.ipn else ("", ())
    rows = con.execute(
        f"select ipn, description, source, symbol, footprint"
        f" from parts_table {where} order by ipn", vals).fetchall()
    if not rows:
        raise Bad("nothing to show")
    for ipn, desc, source, sym, fp in rows:
        refs = []
        for ref, par in con.execute(
                "select ref, parent from ref_table where ipn = ?"
                " order by ref", (ipn,)):
            up = ref_of(con, par)
            refs.append(f"{ref}<{up}" if up else ref)
        print(f"{ipn}  {CLASSES[ipn[0]][0]:12s} {(desc or '')[:52]}")
        print(f"    refs: {' '.join(refs) or '—'}")
        if args.ipn:
            print(f"    source: {source or '—'}  symbol: {sym or '—'}"
                  f"  footprint: {fp or '—'}")
            kids = [r[0] for r in con.execute(
                "select r2.ref from ref_table r2 join ref_table r1"
                " on r2.parent = r1.uuid where r1.ipn = ? order by r2.ref",
                (ipn,))]
            if kids:
                print(f"    children: {' '.join(kids)}")
            nm, mp, mf, ds = con.execute(
                "select name, mpn, manufacturer, datasheet from "
                "parts_table where ipn = ?", (ipn,)).fetchone()
            print(f"    name: {nm or '—'}  mpn: {mp or '—'}  "
                  f"manufacturer: {mf or '—'}")
            print(f"    datasheet: {ds or '—'}")
            for vend, vpn, q, up, st, ck in con.execute(
                    "select vendor, vendor_pn, break_qty,"
                    " unit_price, stock, checked from price_table"
                    " where ipn = ? order by vendor, vendor_pn, break_qty",
                    (ipn,)):
                print(f"    price: {vend} {vpn} "
                      f"{q}+ {up} stock {st if st is not None else '—'}"
                      f" {ck or '—'}")


# ------------------------------------------------------------------- entry

def main(argv):
    ap = argparse.ArgumentParser(prog="table-write.py", add_help=True)
    ap.add_argument("board", help="the KiCad project directory")
    sub = ap.add_subparsers(dest="verb", required=True)

    def fields(p):
        for f in FIELDS:
            p.add_argument(f"--{f.replace('_', '-')}", dest=f)

    a = sub.add_parser("add", help="create a part")
    a.add_argument("--class", dest="cls", required=True)
    a.add_argument("--count", type=int, default=1,
                   help="instances to create. Default 1")
    a.add_argument("--page")
    a.add_argument("--room")
    a.add_argument("--parent", help="reference of the parent instance")
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
    p.add_argument("--parent", help="reference of the parent instance")
    p.set_defaults(run=place)

    r = sub.add_parser("parent", help="set one instance's parent")
    r.add_argument("ref")
    r.add_argument("--under", help="reference of the parent instance")
    r.add_argument("--none", action="store_true", help="clear the parent")
    r.set_defaults(run=reparent)


    d = sub.add_parser("drop", help="remove one instance")
    d.add_argument("ref")
    d.set_defaults(run=drop)

    v = sub.add_parser("price", help="record a vendor price survey")
    v.add_argument("ipn")
    v.add_argument("--vendor", required=True)
    v.add_argument("--vendor-pn", dest="vendor_pn", required=True)
    v.add_argument("--stock", type=int)
    v.add_argument("--checked", help="date the survey was taken")
    v.add_argument("--break", dest="brk", action="append", default=[],
                   metavar="QTY:PRICE", help="one vendor break. Repeatable")
    v.set_defaults(run=price)

    n = sub.add_parser("net", help="name the net on one pin")
    n.add_argument("ref")
    n.add_argument("pin")
    n.add_argument("name", nargs="?")
    n.add_argument("--none", action="store_true", help="clear the net")
    n.set_defaults(run=net)

    w = sub.add_parser("show", help="print parts and their instances")
    w.add_argument("ipn", nargs="?")
    w.set_defaults(run=show)

    args = ap.parse_args(argv[1:])
    con = connect(args.board)
    try:
        if getattr(args, "ipn", None):
            # n0.3: a name or an approved MPN serves anywhere an IPN does
            args.ipn = resolve(con, args.ipn)
        if args.verb in ("place", "set", "price") \
                and not IPN.match(args.ipn):
            raise Bad(f"'{args.ipn}' names no part")
        args.run(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
