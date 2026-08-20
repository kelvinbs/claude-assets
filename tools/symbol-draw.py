#!/usr/bin/env python3
"""symbol-draw - give every part a symbol in the project library.

    symbol-draw.py <board-dir> <ipn> [--redraw] [--lib DIR ...]
    symbol-draw.py <board-dir> --all [--redraw] [--lib DIR ...]

Drawing is the last resort. For each part it runs `copy-kicad-part` first,
and only when that returns null does it run `datasheet-read` and draw the
pins that come back. Either way `parts_table` is told where the symbol is.

Both are run as commands. Nothing is imported from another tool.

`--all` is every part whose `symbol` is null and whose instances put it on a
page. A part on no page is not on a sheet and has no symbol.

The library is merged, never rewritten: a symbol already in it is left alone
unless `--redraw` names it, because it may have been corrected by hand.

"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

GRID = 2.54
FONT = 1.27
PIN_LEN = 2.54
OFFSET = 1.016   # pin_names offset - the gap between the body and a name

SIDES = {"L", "R", "T", "B"}
IPN = re.compile(r"^[A-Z]\d{4}$")


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"symbol-draw: {message}")


# ------------------------------------------------------------------ the record

def connect(board):
    path = Path(board) / "board.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table", "ref_table", "aml_table", "mpn_table"} <= have:
        raise Bad(f"{path} is missing a table. Run db-init")
    return con


def part_row(con, ipn):
    row = con.execute(
        "select ipn, description, symbol, source from parts_table "
        "where ipn = ?", (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    return row


def prefix_of(con, ipn):
    """The reference designator prefix, taken off the instances the record
    already carries. `U1` is a `U`."""
    row = con.execute("select ref from ref_table where ipn = ? and ref is not "
                      "null order by ref", (ipn,)).fetchone()
    if row is None:
        return "U"
    return re.match(r"^([A-Za-z]+)", row[0]).group(1)


def mpn_of(con, ipn):
    row = con.execute("select mpn from aml_table where ipn = ? "
                      "order by rank is not null, rank", (ipn,)).fetchone()
    return row[0] if row else ""


def datasheet_of(con, ipn):
    row = con.execute(
        "select m.datasheet from aml_table a "
        "left join mpn_table m on m.mpn = a.mpn where a.ipn = ? "
        "order by a.rank is not null, a.rank", (ipn,)).fetchone()
    return (row[0] if row else None) or ""


def write_fields(con, ipn, lib_id, letter, was_source):
    """`source` is two letters, symbol then footprint. This tool owns the
    first and does not touch the second."""
    footprint_letter = (was_source or "--")[1:2] or "-"
    con.execute("update parts_table set symbol = ?, source = ? where ipn = ?",
                (lib_id, letter + footprint_letter, ipn))
    con.commit()


# ----------------------------------------------------------------- the library

def library_of(board):
    """The project's library, found where `sym-lib-table` points, or by the
    one `.kicad_sym` in `lib/`."""
    table = Path(board) / "sym-lib-table"
    if table.exists():
        for line in table.read_text().splitlines():
            name = re.search(r'\(name "([^"]+)"\)', line)
            uri = re.search(r'\(uri "\$\{KIPRJMOD\}/lib/([^"]+)"\)', line)
            if name and uri:
                path = Path(board) / "lib" / uri.group(1)
                if path.exists():
                    return name.group(1), path
    found = sorted((Path(board) / "lib").glob("*.kicad_sym"))
    if len(found) == 1:
        return found[0].stem, found[0]
    raise Bad(f"no project library in {board}/lib. Run kicad-init first")


def held(library, name):
    """Whether the library already holds a symbol under this name."""
    return f'(symbol "{name}"' in library.read_text()


def merge(library, name, block, redraw):
    """Add what is missing, leave what is there. A symbol already in the
    library may have been corrected by hand."""
    src = library.read_text()
    needle = f'(symbol "{name}"'
    start = src.find(needle)
    if start >= 0:
        if not redraw:
            return False
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(src)):
            char = src[i]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = not in_string
            elif in_string:
                continue
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    library.write_text(src[:start] + block.strip()
                                       + src[i + 1:])
                    return True
        raise Bad(f"symbol '{name}' does not close")
    close = src.rstrip().rfind(")")
    if close < 0:
        raise Bad(f"{library} does not read as a kicad_symbol_lib")
    library.write_text(src[:close] + block + src[close:])
    return True


# ---------------------------------------------------------------- the drawing

def snap(v):
    return round(v / GRID) * GRID


def rise(v):
    """Up to the next grid line, never down. A body sized by rounding to the
    nearest one can come back smaller than the text it has to hold."""
    return math.ceil(v / GRID - 1e-9) * GRID


def sexp(tag, *body, indent=2):
    pad = "\t" * indent
    inner = "".join(body)
    return f"{pad}({tag}\n{inner}{pad})\n"


def effects(indent, hide=False, justify=None):
    pad = "\t" * indent
    out = f"{pad}(effects\n{pad}\t(font\n{pad}\t\t(size {FONT} {FONT})\n{pad}\t)\n"
    if justify:
        out += f"{pad}\t(justify {justify})\n"
    if hide:
        out += f"{pad}\t(hide yes)\n"
    return out + f"{pad})\n"


def pin_sexp(number, name, etype, x, y, angle):
    return (
        f"\t\t\t(pin {etype} line\n"
        f"\t\t\t\t(at {x} {y} {angle})\n"
        f"\t\t\t\t(length {PIN_LEN})\n"
        f"\t\t\t\t(name \"{name}\"\n{effects(5)}\t\t\t\t)\n"
        f"\t\t\t\t(number \"{number}\"\n{effects(5)}\t\t\t\t)\n"
        f"\t\t\t)\n"
    )


def rect_sexp(x1, y1, x2, y2):
    return (
        f"\t\t\t(rectangle\n"
        f"\t\t\t\t(start {x1} {y1})\n"
        f"\t\t\t\t(end {x2} {y2})\n"
        f"\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n\t\t\t\t\t(type default)\n\t\t\t\t)\n"
        f"\t\t\t\t(fill\n\t\t\t\t\t(type background)\n\t\t\t\t)\n"
        f"\t\t\t)\n"
    )


def property_sexp(name, value, x, y, hide=False, justify=None):
    return (
        f"\t\t(property \"{name}\" \"{value}\"\n"
        f"\t\t\t(at {x} {y} 0)\n{effects(3, hide, justify)}"
        f"\t\t)\n"
    )


def body_size(spec):
    """Size the body to its own pins. Names sit inside, so the width has to
    carry the longest name on each of the left and right."""
    sides = {s: [p for p in spec["pins"] if p[3] == s] for s in SIDES}
    text = 0.9 * FONT  # width of one character at this font size

    def longest(side):
        return max([len(p[1]) for p in sides[side]] or [0])

    # Left and right names read across the body, so they set its width. Top
    # and bottom names read up it, so they take a band off the top and the
    # bottom that the side pins have to start clear of. A name starts OFFSET
    # inside the edge the pin enters by, and every band is rounded up — a
    # band rounded down is a name sitting on the one beside it.
    w = max(len(sides["T"]), len(sides["B"])) * 2 * GRID + 4 * GRID
    w = max(w, (longest("L") + longest("R")) * text + 2 * OFFSET + GRID,
            4 * GRID)

    # FONT is the height of the side name, which is centred on its own row,
    # so half of it reaches up into the band above.
    top = rise(longest("T") * text + OFFSET + FONT) if sides["T"] else GRID
    bottom = rise(longest("B") * text + OFFSET + FONT) if sides["B"] else GRID
    h = max(len(sides["L"]), len(sides["R"])) * GRID + top + bottom + GRID
    h = max(h, 4 * GRID)
    return rise(w / 2), rise(h / 2), sides, top


def build_symbol(spec):
    """Lay the pins out so the symbol reads like the package drawing: the
    left side runs down in the order given, the right side runs up, and the
    top and bottom run left to right."""
    half_w, half_h, sides, top = body_size(spec)
    name = spec["name"]

    drawing = rect_sexp(-half_w, half_h, half_w, -half_h)

    pins = ""
    for i, (number, pname, etype, _) in enumerate(sides["L"]):
        y = snap(half_h - top - i * GRID)
        pins += pin_sexp(number, pname, etype, -half_w - PIN_LEN, y, 0)
    for i, (number, pname, etype, _) in enumerate(reversed(sides["R"])):
        y = snap(half_h - top - i * GRID)
        pins += pin_sexp(number, pname, etype, half_w + PIN_LEN, y, 180)
    # Top and bottom run at twice the pitch and clear of the corners, so a
    # name reading up the body does not land on its neighbour or on the
    # outermost pin of the side next to it.
    for i, (number, pname, etype, _) in enumerate(sides["T"]):
        x = snap(-half_w + 2 * GRID + i * 2 * GRID)
        pins += pin_sexp(number, pname, etype, x, half_h + PIN_LEN, 270)
    for i, (number, pname, etype, _) in enumerate(sides["B"]):
        x = snap(-half_w + 2 * GRID + i * 2 * GRID)
        pins += pin_sexp(number, pname, etype, x, -half_h - PIN_LEN, 90)

    props = (
        property_sexp("Reference", spec["reference"], -half_w, half_h + GRID,
                      justify="left bottom")
        + property_sexp("Value", name, -half_w, -half_h - GRID,
                        justify="left top")
        + property_sexp("Footprint", "", 0, 0, hide=True)
        + property_sexp("Datasheet", spec.get("datasheet", ""), 0, 0, hide=True)
        + property_sexp("Description", spec.get("description", ""), 0, 0,
                        hide=True)
    )

    return (
        f"\t(symbol \"{name}\"\n"
        f"\t\t(pin_names\n\t\t\t(offset 1.016)\n\t\t)\n"
        f"\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
        f"{props}"
        f"\t\t(symbol \"{name}_0_1\"\n{drawing}\t\t)\n"
        f"\t\t(symbol \"{name}_1_1\"\n{pins}\t\t)\n"
        f"\t)\n"
    )



# ------------------------------------------------------------- the two resorts

def try_copy(board, ipn, hint, extra):
    """`copy-kicad-part`, run as a command. Returns the library id it wrote,
    or None."""
    argv = [sys.executable, str(HERE / "copy-kicad-part.py"), str(board),
            hint, "--name", ipn]
    for folder in extra or []:
        argv += ["--lib", folder]
    run = subprocess.run(argv, capture_output=True, text=True)
    if run.returncode != 0:
        raise Bad((run.stderr or run.stdout).strip())
    first = (run.stdout.strip().splitlines() or ["null"])[0]
    if first.startswith("null"):
        return None
    return first.split()[0]


def try_read(board, ipn, extra):
    """`datasheet-read --json`, run as a command. Returns the pins, or None."""
    argv = [sys.executable, str(HERE / "datasheet-read.py"), str(board), ipn,
            "--json"]
    if extra:
        argv += ["--datasheets", extra]
    run = subprocess.run(argv, capture_output=True, text=True)
    if run.returncode != 0:
        return None
    for line in run.stdout.strip().splitlines():
        if line.startswith("{"):
            answer = json.loads(line)
            return answer.get("pins")
    return None


def one(con, board, ipn, nickname, library, args):
    ipn, description, symbol, source = part_row(con, ipn)
    if symbol and not args.redraw:
        print(f"{ipn}  already {symbol}")
        return True

    mpn = mpn_of(con, ipn)
    hint = " ".join(filter(None, [mpn, description]))

    written = try_copy(board, ipn, hint, args.lib)
    if written:
        write_fields(con, ipn, written, "s", source)
        print(f"{ipn}  {written}  s  copied")
        return True

    pins = try_read(board, ipn, args.datasheets)
    if not pins:
        raise Bad(f"{ipn}: no library holds it and no pinout could be read")

    spec = {"name": ipn, "reference": prefix_of(con, ipn), "pins": pins,
            "description": description or "",
            "datasheet": datasheet_of(con, ipn)}
    merge(library, ipn, build_symbol(spec), True)
    write_fields(con, ipn, f"{nickname}:{ipn}", "h", source)
    print(f"{ipn}  {nickname}:{ipn}  h  drawn, {len(pins)} pins")
    return True


# ------------------------------------------------------------------------ run

def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn", nargs="?")
    ap.add_argument("--all", action="store_true",
                    help="every part on a page with no symbol yet")
    ap.add_argument("--redraw", action="store_true",
                    help="replace a symbol the library already holds")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    ap.add_argument("--datasheets", help="the directory of datasheets")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if bool(args.ipn) == bool(args.all):
        raise Bad("name one IPN, or --all")
    if args.ipn and not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")

    nickname, library = library_of(board)
    con = connect(board)
    try:
        if args.ipn:
            targets = [args.ipn]
        else:
            # A part no instance puts on a page is not on a sheet, and a part
            # not on a sheet has no symbol.
            targets = [r[0] for r in con.execute(
                "select p.ipn from parts_table p where p.symbol is null "
                "and exists (select 1 from ref_table r where r.ipn = p.ipn "
                "and trim(coalesce(r.page,'')) <> '') order by p.ipn")]
            skipped = [r[0] for r in con.execute(
                "select p.ipn from parts_table p where p.symbol is null "
                "and not exists (select 1 from ref_table r where r.ipn = p.ipn "
                "and trim(coalesce(r.page,'')) <> '') order by p.ipn")]
            if skipped:
                print(f"{len(skipped)} part(s) on no page, not drawn: "
                      + " ".join(skipped))
            if not targets:
                print("every part on a page has a symbol")
                return 0

        failed = []
        for ipn in targets:
            try:
                one(con, board, ipn, nickname, library, args)
            except Bad as exc:
                if len(targets) == 1:
                    raise
                print(str(exc))
                failed.append(ipn)
    finally:
        con.close()

    if failed:
        print(f"\n{len(failed)} of {len(targets)} without a symbol: "
              + " ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
