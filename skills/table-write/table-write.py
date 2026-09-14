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
    table-write.py <board-dir> bus    [<name> <net>... | --drop <net>...]
    table-write.py <board-dir> show   [<ipn>]

It adds what is missing and leaves what is there. An instance is removed only
by naming its reference, one at a time.

An instance is (uuid, path) — T2.4. On a root page the path is ''. In a
sub-sheet it is the chain of sheet-instance uuids down to that sheet, so
one symbol drawn once in a reused sheet is one row per instance of the
sheet, each with its own reference. A sub-sheet is a part of class B; its
instances are rows like any other, drawn as sheet symbols.
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
    "B": ("block, sub-sheet", "SH"),
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


def instance_of(con, ref):
    """The row a reference names: (uuid, path, page)."""
    row = con.execute("select uuid, path, page from ref_table where ref = ?",
                      (ref,)).fetchone()
    if row is None:
        raise Bad(f"{ref} names no instance")
    return row


def sheet_part(con, page):
    """The class-B part whose name is this page, else None: a page that is
    a sub-sheet, instanced on other pages."""
    row = con.execute("select ipn from parts_table where name = ? "
                      "and ipn glob 'B[0-9][0-9][0-9][0-9]'",
                      (page,)).fetchone()
    return row[0] if row else None


def paths_of_sheet(con, page):
    """Every instance path of a sub-sheet: for each instance row of its
    class-B part, that row's own path extended by its uuid."""
    ipn = sheet_part(con, page)
    if ipn is None:
        return [""]
    out = []
    for u, path in con.execute("select uuid, path from ref_table where ipn = ? "
                               "order by ref", (ipn,)):
        out.append(f"{path}/{u}" if path else u)
    if not out:
        raise Bad(f"sub-sheet {page} has no instances yet. Place its part "
                  f"({ipn}) on a page first")
    return out


def parent_for(con, parent_ref, page, path):
    """(parent uuid, parent path) for a child on `page` at `path`. A parent
    drawn in the same sub-sheet is matched instance for instance."""
    if not parent_ref:
        return None, None
    pu, ppath, ppage = instance_of(con, parent_ref)
    if ppage == page and ppath and path:
        return pu, path
    return pu, ppath


def new_symbol(con, ipn, prefix, page, room, parent_ref):
    """One symbol, drawn once: a fresh uuid, one row per instance path of
    its page, each with the next free reference. Returns the refs."""
    u = str(uuid.uuid4())
    refs = []
    for path in paths_of_sheet(con, page) if page else [""]:
        ref = next_ref(con, prefix)
        parent, parent_path = parent_for(con, parent_ref, page, path)
        con.execute("insert into ref_table (uuid, ipn, parent, parent_path, "
                    "ref, page, room, path) values (?,?,?,?,?,?,?,?)",
                    (u, ipn, parent, parent_path, ref, page, room, path))
        refs.append(ref)
    return refs


def replicate_sheet(con, ipn, new_paths):
    """A sub-sheet gained instances: every symbol drawn in it gets a row
    for each new path, so each instance carries its own references."""
    name = con.execute("select name from parts_table where ipn = ?",
                       (ipn,)).fetchone()[0]
    if not name:
        raise Bad(f"{ipn} is a sub-sheet and needs a --name: it names the "
                  "page the sheet is drawn on")
    added = 0
    rows = con.execute(
        "select uuid, ipn, parent, ref, room, unit, page from ref_table "
        "where page = ? group by uuid order by ref", (name,)).fetchall()
    for u, cipn, parent, ref, room, unit, page in rows:
        prefix = REF.match(ref).group(1) if ref and REF.match(ref) else "U"
        for path in new_paths:
            if con.execute("select 1 from ref_table where uuid = ? and path = ?",
                           (u, path)).fetchone():
                continue
            con.execute("insert into ref_table (uuid, ipn, parent, parent_path, "
                        "ref, page, room, unit, path) values (?,?,?,?,?,?,?,?,?)",
                        (u, cipn, parent, path if parent else None,
                         next_ref(con, prefix), page, room, unit, path))
            added += 1
    return added


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
    if args.parent:
        instance_of(con, args.parent)

    con.execute(
        f"insert into parts_table (ipn, {', '.join(FIELDS)}) "
        f"values (?, {', '.join('?' * len(FIELDS))})",
        (ipn,) + tuple(values[f] for f in FIELDS))
    print(f"{ipn}  {category}")

    if letter == "B":
        if not values["name"]:
            raise Bad("a sub-sheet needs --name: the page it is drawn on")
        con.execute("update parts_table set symbol = ? where ipn = ?",
                    (f"sheet:{values['name']}", ipn))
    for _ in range(args.count):
        refs = new_symbol(con, ipn, prefix, args.page, args.room, args.parent)
        print(f"    {' '.join(refs)}  {args.page or '—'}")
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
    """Raise the symbol count of a part to --count. A symbol is drawn once;
    in a sub-sheet it is one row per instance of the sheet."""
    part(con, args.ipn)
    prefix = CLASSES[args.ipn[0]][1]
    have = con.execute("select count(distinct uuid) from ref_table "
                       "where ipn = ?", (args.ipn,)).fetchone()[0]
    if args.count < have:
        raise Bad(f"{args.ipn} has {have} symbols. This tool does not "
                  f"remove them — name the reference with drop")
    page = args.page
    if page is None:
        row = con.execute("select page from ref_table where ipn = ? "
                          "and page is not null limit 1",
                          (args.ipn,)).fetchone()
        page = row[0] if row else None
    if args.parent:
        instance_of(con, args.parent)
    before = {r[0] for r in con.execute(
        "select uuid from ref_table where ipn = ?", (args.ipn,))}
    for _ in range(args.count - have):
        refs = new_symbol(con, args.ipn, prefix, page, args.room, args.parent)
        print(f"{args.ipn}  {' '.join(refs)}  {page or '—'}")
    if args.page or args.room:
        # the rows just made, and any row of the part still without a page
        sets, vals = [], []
        if args.page:
            sets.append("page = ?"); vals.append(args.page)
        if args.room:
            sets.append("room = ?"); vals.append(args.room)
        con.execute(f"update ref_table set {', '.join(sets)} where ipn = ? "
                    "and (page is null or page = '')",
                    tuple(vals) + (args.ipn,))
    if args.ipn.startswith("B"):
        fresh = [r[0] for r in con.execute(
            "select uuid from ref_table where ipn = ?", (args.ipn,))
            if r[0] not in before]
        new_paths = [f"{p}/{u}" if p else u for u, p in con.execute(
            "select uuid, path from ref_table where ipn = ?", (args.ipn,))
            if u in fresh]
        if new_paths:
            n = replicate_sheet(con, args.ipn, new_paths)
            if n:
                print(f"{args.ipn}  {n} row(s) added for the symbols drawn "
                      "in the sheet")
    con.commit()
    print(f"{args.ipn}  {max(args.count, have)} symbol(s)")


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
    page = con.execute("select page from ref_table where ref = ?",
                       (args.ref,)).fetchone()[0]
    for path, in con.execute("select path from ref_table where uuid = ?",
                             (child,)).fetchall():
        parent, parent_path = (parent_for(con, args.under, page, path)
                               if new else (None, None))
        con.execute("update ref_table set parent = ?, parent_path = ? "
                    "where uuid = ? and path = ?",
                    (parent, parent_path, child, path))
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
    """Remove one instance by reference. A sub-sheet instance takes the
    rows drawn under it. A symbol in a sub-sheet is one drawing, so its
    rows go together. Nets go with a symbol's last row."""
    row = con.execute("select ipn, uuid, path from ref_table where ref = ?",
                      (args.ref,)).fetchone()
    if row is None:
        raise Bad(f"{args.ref} is not in ref_table")
    ipn, u, path = row
    if ipn.startswith("B"):
        inst = f"{path}/{u}" if path else u
        n = con.execute("delete from ref_table where path = ? "
                        "or path like ?", (inst, inst + "/%")).rowcount
        if n:
            print(f"{args.ref}  {n} row(s) under it removed")
        con.execute("delete from ref_table where uuid = ? and path = ?",
                    (u, path))
    elif path:
        refs = [r[0] for r in con.execute(
            "select ref from ref_table where uuid = ? order by ref", (u,))]
        con.execute("delete from ref_table where uuid = ?", (u,))
        print(f"{args.ref}  one drawing in a sub-sheet: {' '.join(refs)} "
              "removed together")
    else:
        con.execute("delete from ref_table where uuid = ? and path = ''",
                    (u,))
    for (gone,) in con.execute(
            "select distinct uuid from net_table where uuid not in "
            "(select uuid from ref_table)").fetchall():
        con.execute("delete from net_table where uuid = ?", (gone,))
    con.commit()
    print(f"{args.ref}  removed from {ipn}")
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


def bus(con, args):
    """Group nets into a bus. `bus` alone lists. `bus NAME NET...` puts the
    nets in NAME, moving any that were elsewhere. `--drop NET...` takes
    them out of any bus."""
    if args.drop:
        for n in args.nets:
            k = con.execute("delete from bus_table where net = ?",
                            (n,)).rowcount
            print(f"{n}  {'out of its bus' if k else 'was in no bus'}")
        con.commit()
        return
    if not args.name:
        for b, n in con.execute("select bus, net from bus_table "
                                "order by bus, net"):
            print(f"{b}  {n}")
        return
    if not args.nets:
        raise Bad("bus wants the nets to put in it")
    for n in args.nets:
        con.execute("insert into bus_table (net, bus) values (?,?) "
                    "on conflict(net) do update set bus = excluded.bus",
                    (n, args.name))
        print(f"{args.name}  {n}")
    con.commit()


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

    b = sub.add_parser("bus", help="group nets into a bus")
    b.add_argument("name", nargs="?", help="the bus")
    b.add_argument("nets", nargs="*", help="member nets")
    b.add_argument("--drop", action="store_true",
                   help="take the named nets out of any bus")
    b.set_defaults(run=bus)

    w = sub.add_parser("show", help="print parts and their instances")
    w.add_argument("ipn", nargs="?")
    w.set_defaults(run=show)

    args = ap.parse_args(argv[1:])
    if args.verb == "bus" and args.drop and args.name:
        args.nets = [args.name] + args.nets
        args.name = None
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
