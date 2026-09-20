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
    table-write.py <board-dir> unplace <ref>
    table-write.py <board-dir> room   <ref> <name> [--under <room|ref|ref.unit>] [--corner nw|ne|sw|se] | --none
    table-write.py <board-dir> board  <name> --page P | --ref R | --none | --show
    table-write.py <board-dir> show   [<ipn>]

It adds what is missing and leaves what is there. An instance is removed only
by naming its reference, one at a time.

A node is one id — T2.4. A drawing in a sub-sheet is one node per
sub-sheet it is the chain of sheet-instance uuids down to that sheet, so
one symbol drawn once in a reused sheet is one row per instance of the
sheet, each with its own reference. A sub-sheet is a part of class B; its
instances are rows like any other, drawn as sheet symbols.
"""

import argparse
import json
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
          "datasheet", "footprint", "sim_model", "sim_params")

SIM_MODELS = ("R", "C", "L", "opamp", "rnet")
SIM_KINDS = {"ac": ".ac dec 100 1 10meg", "tran": ".tran 1u 10m",
             "dc": ".op", "op": ".op",
             # the output node and the input source are the User's to set
             "noise": ".noise v(OUT) V1 dec 100 1 10meg"}


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
    """The id a reference names, for `parent`."""
    row = con.execute("select id from ref_table where ref = ? "
                      "and kind = 'part'", (ref,)).fetchone()
    if row is None:
        raise Bad(f"{ref} names no instance")
    return row[0]


def instance_of(con, ref):
    """The node a reference names: (id, page)."""
    row = con.execute("select id, page from ref_table where ref = ? "
                      "and kind = 'part'", (ref,)).fetchone()
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


def sheets_of(con, page):
    """Every sheet-instance node of a sub-sheet page: one id per drawing of
    its class-B part. `[None]` for a root page - T2.4, the path is walked
    from these, never stored."""
    ipn = sheet_part(con, page)
    if ipn is None:
        return [None]
    out = [i for (i,) in con.execute(
        "select id from ref_table where ipn = ? and kind = 'part' "
        "order by ref", (ipn,))]
    if not out:
        raise Bad(f"sub-sheet {page} has no instances yet. Place its part "
                  f"({ipn}) on a page first")
    return out


def under(con, sheet_id, nid):
    """True when `nid` is `sheet_id` or sits under it by the parent chain."""
    walk = nid
    while walk:
        if walk == sheet_id:
            return True
        walk = con.execute("select parent from ref_table where id = ?",
                           (walk,)).fetchone()[0]
    return False


def parent_for(con, parent_ref, sheet_id):
    """The parent id for a child in this sheet instance. A parent drawn in
    the same sub-sheet is matched instance for instance; otherwise the
    sheet-instance node itself carries the child, so its path derives."""
    if not parent_ref:
        return sheet_id
    for (pid,) in con.execute("select id from ref_table where ref = ? "
                              "and kind = 'part'", (parent_ref,)):
        if sheet_id is None or under(con, sheet_id, pid):
            return pid
    raise Bad(f"{parent_ref} names no instance in this sheet")


def new_symbol(con, ipn, prefix, page, parent_ref):
    """One symbol: one node per sheet instance of its page, each with a
    fresh id, one shared `sym_uuid`, and the next free reference."""
    sym = str(uuid.uuid4())
    refs = []
    for sheet_id in sheets_of(con, page) if page else [None]:
        ref = next_ref(con, prefix)
        con.execute("insert into ref_table (id, sym_uuid, kind, ipn, parent, "
                    "ref, page) values (?,?,'part',?,?,?,?)",
                    (str(uuid.uuid4()), sym, ipn,
                     parent_for(con, parent_ref, sheet_id), ref, page))
        refs.append(ref)
    return refs


def replicate_sheet(con, ipn, new_sheets):
    """A sub-sheet gained instances: every symbol drawn in it gets a node
    under each new sheet instance, so each carries its own references."""
    name = con.execute("select name from parts_table where ipn = ?",
                       (ipn,)).fetchone()[0]
    if not name:
        raise Bad(f"{ipn} is a sub-sheet and needs a --name: it names the "
                  "page the sheet is drawn on")
    added = 0
    rows = con.execute(
        "select sym_uuid, ipn, ref, unit from ref_table where page = ? "
        "and kind = 'part' group by sym_uuid order by ref", (name,)).fetchall()
    for sym, cipn, ref, unit in rows:
        prefix = REF.match(ref).group(1) if ref and REF.match(ref) else "U"
        for sheet_id in new_sheets:
            if con.execute("select 1 from ref_table r where r.sym_uuid = ? "
                           "and r.parent is not null", (sym,)).fetchone() \
                    and any(under(con, sheet_id, i) for (i,) in con.execute(
                        "select id from ref_table where sym_uuid = ?", (sym,))):
                continue
            con.execute("insert into ref_table (id, sym_uuid, kind, ipn, "
                        "parent, ref, page, unit) values (?,?,'part',?,?,?,?,?)",
                        (str(uuid.uuid4()), sym, cipn, sheet_id,
                         next_ref(con, prefix), name, unit))
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
        refs = new_symbol(con, ipn, prefix, args.page, args.parent)
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
    if changes.get("sim_model") and changes["sim_model"] not in SIM_MODELS:
        raise Bad(f"sim_model must be one of {' '.join(SIM_MODELS)}, "
                  f"found '{changes['sim_model']}'")
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
    have = con.execute("select count(distinct id) from ref_table "
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
        "select id from ref_table where ipn = ?", (args.ipn,))}
    for _ in range(args.count - have):
        refs = new_symbol(con, args.ipn, prefix, page, args.parent)
        print(f"{args.ipn}  {' '.join(refs)}  {page or '—'}")
    if args.page:
        # the rows just made, and any row of the part still without a page
        sets, vals = [], []
        if args.page:
            sets.append("page = ?"); vals.append(args.page)

        con.execute(f"update ref_table set {', '.join(sets)} where ipn = ? "
                    "and (page is null or page = '')",
                    tuple(vals) + (args.ipn,))
    if args.ipn.startswith("B"):
        new_sheets = [r[0] for r in con.execute(
            "select id from ref_table where ipn = ? and kind = 'part'",
            (args.ipn,)) if r[0] not in before]
        if new_sheets:
            n = replicate_sheet(con, args.ipn, new_sheets)
            if n:
                print(f"{args.ipn}  {n} row(s) added for the symbols drawn "
                      "in the sheet")
    con.commit()
    print(f"{args.ipn}  {max(args.count, have)} symbol(s)")


def reparent(con, args):
    row = con.execute("select id, parent from ref_table where ref = ?",
                      (args.ref,)).fetchone()
    if row is None:
        raise Bad(f"{args.ref} is not in ref_table")
    child, was = row
    if args.none:
        new = None
    else:
        if not args.under:
            raise Bad("give --under <ref> or --none")
        # A27: no cycle check here. `parent` is written by this verb, by
        # `room` twice, and by `add` on insert; a guard on one of the three
        # protects nothing. A ring is caught where it bites, by the bounded
        # walks of A28
        new = parent_uuid(con, args.under)
    # every node under the reference: every unit of a package, every
    # instance of a sub-sheet
    for (u,) in con.execute("select id from ref_table where ref = ? "
                            "and kind = 'part'", (args.ref,)).fetchall():
        con.execute("update ref_table set parent = ? where id = ?",
                    (parent_uuid(con, args.under) if new else None, u))
    con.commit()
    print(f"{args.ref}  parent {ref_of(con, was) or '—'} -> "
          f"{args.under if new else '—'}")


def ref_of(con, u):
    if u is None:
        return None
    row = con.execute("select ref from ref_table where id = ?",
                      (u,)).fetchone()
    return row[0] if row else None


def drop(con, args):
    """Remove one instance by reference: every node the reference names —
    every unit of a package, every instance of a sub-sheet. A sub-sheet
    instance takes everything under it. Nets go with the node."""
    rows = con.execute("select ipn, id from ref_table where ref = ? "
                       "and kind = 'part' order by id",
                       (args.ref,)).fetchall()
    if not rows:
        raise Bad(f"{args.ref} is not in ref_table")
    ipn = rows[0][0]
    gone = 0
    for _, nid in rows:
        if ipn.startswith("B"):
            # a sheet instance carries a subtree: take it whole
            todo, sub, seen = [nid], [], {nid}
            while todo:
                x = todo.pop()
                kids = [k for (k,) in con.execute(
                    "select id from ref_table where parent = ?", (x,))
                    if k not in seen]
                seen.update(kids)
                sub.extend(kids); todo.extend(kids)
            for k in sub:
                con.execute("delete from net_table where id = ?", (k,))
                con.execute("delete from ref_table where id = ?", (k,))
            if sub:
                print(f"{args.ref}  {len(sub)} row(s) under it removed")
        con.execute("delete from net_table where id = ?", (nid,))
        con.execute("delete from ref_table where id = ?", (nid,))
        gone += 1
    con.commit()
    print(f"{args.ref}  removed from {ipn}"
          + (f", {gone} node(s)" if gone > 1 else ""))


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
            " break_qty, unit_price, stock, checked, library)"
            " values (?,?,?,?,?,?,?,?)"
            " on conflict(ipn, vendor, vendor_pn, break_qty) do update set"
            " unit_price = excluded.unit_price,"
            " stock = excluded.stock,"
            " checked = excluded.checked,"
            " library = coalesce(excluded.library, price_table.library)",
            (args.ipn, args.vendor, args.vendor_pn, qty, unit,
             args.stock, args.checked, args.library))
    con.commit()
    print(f"{args.ipn}  {args.vendor} {args.vendor_pn}:"
          f" {len(breaks)} breaks recorded")


def unit_of_pin(con, board, ref, pin):
    """The uuid of the drawing that carries this pin. A reference names a
    package; a multi-unit package is several drawings under it, and a pin
    belongs to the one that draws it (T2.6a). The part file's `units`
    says which; with no `units` the part is one drawing."""
    rows = con.execute(
        "select r.id, r.unit, r.ipn, p.name from ref_table r "
        "join parts_table p using(ipn) where r.ref = ? order by r.unit",
        (ref,)).fetchall()
    if not rows:
        raise Bad(f"{ref} names no instance")
    if len(rows) == 1:
        return rows[0][0]
    ipn, name = rows[0][2], rows[0][3]
    path = Path(board) / "parts" / f"{ipn}-{name}.json"
    if not path.exists():
        raise Bad(f"{ref} is drawn in {len(rows)} units and its part file "
                  f"{path.name} is missing, so the unit that carries pin "
                  f"{pin} cannot be known. datasheet-read first")
    data = json.loads(path.read_text())
    units = data.get("units")
    if not units:
        pins = {str(x[0]) for x in data.get("pins", [])}
        if str(pin) not in pins:
            raise Bad(f"{ref} has no pin {pin}")
        raise Bad(f"{ref} is drawn in {len(rows)} units and {path.name} has "
                  "no `units` key, so the unit that carries pin "
                  f"{pin} cannot be known. datasheet-read first")
    by_unit = {r[1] or 1: r[0] for r in rows}
    for n, numbers in (units.items() if isinstance(units, dict)
                       else enumerate(units, 1)):
        if str(pin) in {str(x) for x in numbers}:
            n = int(n)
            if n not in by_unit:
                raise Bad(f"{ref} has no unit {n} in the record, and pin "
                          f"{pin} belongs to it")
            return by_unit[n]
    raise Bad(f"{ref} has no pin {pin}: {path.name} gives it to no unit")


def net(con, args):
    """Name the net on one pin of one instance, or clear it. Upsert on
    (id, pin). The pin picks the drawing: on a multi-unit package the
    unit that carries it, per the part file (T2.6a). The sheet takes a
    label at that pin on the next place or push."""
    u = unit_of_pin(con, args.board, args.ref, args.pin)
    if args.none:
        n = con.execute("delete from net_table where id = ? and pin = ?",
                        (u, args.pin)).rowcount
        con.commit()
        print(f"{args.ref}  pin {args.pin}: net cleared" if n
              else f"{args.ref}  pin {args.pin}: no net to clear")
        return
    if not args.name:
        raise Bad("net wants a name, or --none")
    con.execute(
        "insert into net_table (id, pin, net) values (?,?,?)"
        " on conflict(id, pin) do update set net = excluded.net",
        (u, args.pin, args.name))
    con.commit()
    print(f"{args.ref}  pin {args.pin}: {args.name}")


def room(con, args):
    """Put an instance in a room. A room is a row of its own with a uuid
    and a parent, so rooms nest: `room R3 Filter_I --under U15.2` makes
    the room a child of U15's second unit and the instance a child of the
    room. Without `--under` the room takes the parent the instance has
    today, so nothing moves but the level. `--none` puts the instance back
    under the room's own parent."""
    import uuid as _u
    ref = args.ref
    rows = con.execute("select id, page, parent from ref_table where ref = ? "
                       "and kind = 'part'", (ref,)).fetchall()
    if not rows:
        raise Bad(f"no instance {ref}")
    if args.none:
        n = 0
        for u, page, par in rows:
            up = con.execute("select parent from ref_table "
                             "where kind = 'room' and id = ?",
                             (par,)).fetchone()
            con.execute("update ref_table set parent = ? where id = ?",
                        (up[0] if up else None, u))
            n += 1
        con.commit()
        print(f"{ref}  out of its room on {n} row(s)")
        return
    name = args.name
    if not name:
        raise Bad("room wants a name, or --none")
    under = None
    if args.under:
        under = resolve_holder(con, args.under)
    n = 0
    for u, page, par in rows:
        hold = under if under else par
        got = con.execute("select id from ref_table where kind = 'room' "
                          "and name = ? and page is ? and parent is ?",
                          (name, page, hold or None)).fetchone()
        if got:
            ru = got[0]
            if args.corner:
                con.execute("update ref_table set corner = ? where id = ?",
                            (args.corner, ru))
        else:
            ru = str(_u.uuid4())
            con.execute("insert into ref_table (id, kind, name, parent, "
                        "page, corner) values (?,'room',?,?,?,?)",
                        (ru, name, hold or None, page, args.corner))
        con.execute("update ref_table set parent = ? where id = ?",
                    (ru, u))
        n += 1
    con.commit()
    print(f"{ref}  room {name}"
          + (f" under {args.under}" if args.under else "")
          + f" on {n} row(s)")


def resolve_holder(con, spec):
    """`U15`, `U15.2` or a room name: the id a parent names. A unit is
    named with a dot, since one id names one node - T2.4."""
    ref, _, unit = spec.partition(".")
    q = ("select id from ref_table where kind = 'part' and ref = ?"
         + (" and unit = ?" if unit else ""))
    got = con.execute(q, (ref, int(unit)) if unit else (ref,)).fetchone()
    if got:
        return got[0]
    got = con.execute("select id from ref_table where kind = 'room' "
                      "and name = ?", (spec,)).fetchone()
    if got:
        return got[0]
    raise Bad(f"{spec} names no instance, no unit and no room")

def inherit_board(con):
    """A row with a page and no board takes the board of its page. The
    board is a property of the page — every row on one page is on one
    board — so a row made after the page was assigned does not rot."""
    return con.execute(
        "update ref_table set board = (select r2.board from ref_table r2 "
        "where r2.page = ref_table.page and r2.board is not null limit 1) "
        "where board is null and page is not null and page <> ''").rowcount


def board_verb(con, args):
    """Say which board a thing is on. `board` is a column of `ref_table`
    beside the room: the page selects the sheet file, the board selects
    the physical board. Set it by page — a page is on one board — or by
    one reference."""
    if args.show:
        rows = con.execute(
            "select coalesce(board, '\u2014'), coalesce(page, '\u2014'), "
            "count(*) from ref_table group by 1, 2 order by 1, 2").fetchall()
        print("| board | page | rows |")
        print("|---|---|---|")
        for b, pg, n in rows:
            print(f"| {b} | {pg} | {n} |")
        return
    name = None if args.none else args.name
    if name is None and not args.none:
        raise Bad("board wants a name, or --none, or --show")
    if args.page:
        n = con.execute("update ref_table set board = ? where page = ?",
                        (name, args.page)).rowcount
        where = f"page {args.page}"
    elif args.ref:
        instance_of(con, args.ref)
        n = con.execute("update ref_table set board = ? where ref = ?",
                        (name, args.ref)).rowcount
        where = args.ref
    else:
        raise Bad("board wants --page or --ref")
    con.commit()
    print(f"{where}  board {name or '\u2014'} on {n} row(s)")


def unplace(con, args):
    """Send an instance back to the packer: its place and mark cleared,
    every row of the drawing. The symbol on the sheet stays where it is
    until the page is placed afresh."""
    instance_of(con, args.ref)
    # every row under the reference: every unit of a package, every path
    n = con.execute("update ref_table set x = null, y = null, rot = null, "
                    "placed = null where id in (select id from ref_table "
                    "where ref = ?)", (args.ref,)).rowcount
    con.commit()
    print(f"{args.ref}  place cleared on {n} row(s); the packer lays it "
          "next time the page is placed afresh")


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


# ------------------------------------------------------------- simulation

RAIL = re.compile(r"^(-?\d+)V(\d*)(?:_.*)?$")


def rail_volts(net):
    """A net named as a voltage, `3V3`, `-5V0`, `5V0_CM5`, else None."""
    m = RAIL.match(net)
    if not m:
        return None
    whole, frac = m.group(1), m.group(2)
    return float(f"{whole}.{frac or '0'}")


def descendants(con, roots):
    """Every instance uuid under the given instance uuids by the parent
    chain, the roots included."""
    seen = set(roots)
    frontier = list(roots)
    while frontier:
        marks = ",".join("?" * len(frontier))
        nxt = [u for (u,) in con.execute(
            f"select distinct id from ref_table where parent in ({marks})",
            frontier) if u not in seen]
        seen.update(nxt)
        frontier = nxt
    return seen


def pin_types(con, board):
    """{uuid: {pin number: type}} from the part files, for every instance
    whose part has one. A part with no file gives nothing."""
    out = {}
    files = {}
    for u, ipn, name in con.execute(
            "select distinct r.id, r.ipn, p.name from ref_table r "
            "join parts_table p using(ipn)"):
        if ipn not in files:
            path = Path(board) / "parts" / f"{ipn}-{name}.json"
            types = {}
            if name and path.exists():
                try:
                    for pin in json.loads(path.read_text()).get("pins", []):
                        types[str(pin[0])] = pin[2]
                except (ValueError, IndexError, TypeError):
                    types = {}
            files[ipn] = types
        if files[ipn]:
            out[u] = files[ipn]
    return out


def sim_instance(con, name):
    row = con.execute("select name, kind, directive from sim_table "
                      "where name = ?", (name,)).fetchone()
    if row is None:
        raise Bad(f"no simulation named {name}")
    return row


def sim_boundary(con, board, blocks):
    """The nets of the blocks' parts that also sit on a pin outside them:
    [(net, source or None, why)]. A rail, a net named as a voltage, gets
    `dc <V>`; a net an outside `output` pin drives gets `ac 1`; the rest
    have no source and are named."""
    roots = [instance_of(con, b)[0] for b in blocks]
    inside = descendants(con, roots)
    types = pin_types(con, board)
    on_inside, on_outside = {}, {}
    for u, pin, net in con.execute("select id, pin, net from net_table"):
        side = on_inside if u in inside else on_outside
        side.setdefault(net, []).append((u, pin))
    out = []
    for net in sorted(on_inside):
        if net not in on_outside or net == "GND":
            continue
        volts = rail_volts(net)
        if volts is not None:
            out.append((net, f"dc {volts:g}", "rail"))
            continue
        driven = any(types.get(u, {}).get(str(pin)) in ("output", "power_out")
                     for u, pin in on_outside[net])
        if driven:
            out.append((net, "ac 1", "driven from outside"))
        else:
            out.append((net, None, "no outside output pin; a load, or "
                        "set a source"))
    return out


def sim_add(con, args):
    kind = args.kind.lower()
    if kind not in SIM_KINDS:
        raise Bad(f"kind must be one of {' '.join(SIM_KINDS)}, "
                  f"found '{args.kind}'")
    if con.execute("select 1 from sim_table where name = ?",
                   (args.name,)).fetchone():
        raise Bad(f"simulation {args.name} exists. `sim drop` it first")
    for b in args.block:
        instance_of(con, b)
    con.execute("insert into sim_table (name, kind, directive) "
                "values (?, ?, ?)", (args.name, kind, SIM_KINDS[kind]))
    for b in args.block:
        con.execute("insert into sim_net_table (name, block, net, source) "
                    "values (?, ?, '', null)", (args.name, b))
    found = sim_boundary(con, args.board, args.block)
    for net, source, _ in found:
        con.execute("insert into sim_net_table (name, block, net, source) "
                    "values (?, '', ?, ?)", (args.name, net, source))
    con.commit()
    print(f"{args.name}  {kind}  {SIM_KINDS[kind]}")
    print(f"    blocks: {' '.join(args.block)}")
    for net, source, why in found:
        print(f"    {net:16s} {source or '—':10s} {why}")
    if not found:
        print("    no boundary net: nothing outside the blocks shares a net")


def sim_set(con, args):
    sim_instance(con, args.name)
    if args.directive is not None:
        con.execute("update sim_table set directive = ? where name = ?",
                    (args.directive, args.name))
        con.commit()
        print(f"{args.name}  directive: {args.directive}")
        return
    if not args.net:
        raise Bad("sim set wants <net> <source>, <net> --none, or "
                  "--directive")
    if args.none:
        n = con.execute("delete from sim_net_table where name = ? and "
                        "net = ?", (args.name, args.net)).rowcount
        con.commit()
        print(f"{args.name}  {args.net}: source cleared on {n} row(s)")
        return
    if not args.source:
        raise Bad("sim set wants a source: `dc 3.3`, `ac 1`, or --none")
    con.execute("insert into sim_net_table (name, block, net, source) "
                "values (?, '', ?, ?) on conflict (name, block, net) "
                "do update set source = excluded.source",
                (args.name, args.net, args.source))
    con.commit()
    print(f"{args.name}  {args.net}: {args.source}")


def sim_drop(con, args):
    sim_instance(con, args.name)
    n = con.execute("select count(*) from sim_net_table where name = ?",
                    (args.name,)).fetchone()[0]
    con.execute("delete from sim_table where name = ?", (args.name,))
    con.commit()
    print(f"{args.name}  dropped, {n} block and source row(s) with it")


def sim_show(con, args):
    rows = con.execute("select name, kind, directive from sim_table "
                       "order by name").fetchall()
    if not rows:
        print("no simulation")
        return
    for name, kind, directive in rows:
        print(f"{name}  {kind}  {directive or '—'}")
        blocks = [b for (b,) in con.execute(
            "select block from sim_net_table where name = ? and block != ''"
            " order by block", (name,))]
        print(f"    blocks: {' '.join(blocks) or '—'}")
        for net, source in con.execute(
                "select net, source from sim_net_table where name = ? "
                "and net != '' order by net", (name,)):
            print(f"    {net:16s} {source or '—'}")


def sim(con, args):
    {"add": sim_add, "set": sim_set, "drop": sim_drop,
     "show": sim_show}[args.op](con, args)


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
    v.add_argument("--library", help="the vendor's library tier: JLCPCB "
                   "basic or extended")
    v.add_argument("--break", dest="brk", action="append", default=[],
                   metavar="QTY:PRICE", help="one vendor break. Repeatable")
    v.set_defaults(run=price)

    n = sub.add_parser("net", help="name the net on one pin")
    n.add_argument("ref")
    n.add_argument("pin")
    n.add_argument("name", nargs="?")
    n.add_argument("--none", action="store_true", help="clear the net")
    n.set_defaults(run=net)

    rm = sub.add_parser("room", help="put an instance in a room")
    rm.add_argument("ref")
    rm.add_argument("name", nargs="?")
    rm.add_argument("--under", help="the room, instance or unit this room sits in")
    rm.add_argument("--corner", choices=("nw", "ne", "sw", "se"),
                    help="which corner of the box the name is placed at")
    rm.add_argument("--none", action="store_true", help="out of its room")
    rm.set_defaults(run=room)

    bd = sub.add_parser("board", help="which board a thing is on")
    bd.add_argument("name", nargs="?", help="the board")
    bd.add_argument("--page", help="every row on this page")
    bd.add_argument("--ref", help="one instance")
    bd.add_argument("--none", action="store_true", help="clear the board")
    bd.add_argument("--show", action="store_true", help="board by page")
    bd.set_defaults(run=board_verb)

    x = sub.add_parser("unplace", help="clear an instance's place")
    x.add_argument("ref")
    x.set_defaults(run=unplace)

    b = sub.add_parser("bus", help="group nets into a bus")
    b.add_argument("name", nargs="?", help="the bus")
    b.add_argument("nets", nargs="*", help="member nets")
    b.add_argument("--drop", action="store_true",
                   help="take the named nets out of any bus")
    b.set_defaults(run=bus)

    sm = sub.add_parser("sim", help="a simulation instance: add, set, drop, "
                        "show")
    smsub = sm.add_subparsers(dest="op", required=True)
    sa = smsub.add_parser("add", help="tag blocks for a simulation")
    sa.add_argument("name")
    sa.add_argument("kind", help="ac, tran, dc or op")
    sa.add_argument("--block", action="append", required=True,
                    help="reference of a block instance. Repeatable")
    ss = smsub.add_parser("set", help="a source on a net, or the directive")
    ss.add_argument("name")
    ss.add_argument("net", nargs="?")
    ss.add_argument("source", nargs="?", help="`dc 3.3`, `ac 1`")
    ss.add_argument("--none", action="store_true", help="clear the source")
    ss.add_argument("--directive", help="the spice line, `.ac dec 100 1 10meg`")
    sd = smsub.add_parser("drop", help="remove a simulation instance")
    sd.add_argument("name")
    smsub.add_parser("show", help="every simulation instance")
    sm.set_defaults(run=sim)

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
        if inherit_board(con):
            con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
