#!/usr/bin/env python3
"""symbol-draw — copy or draw a symbol into the project library.

    symbol-draw.py <board-dir> <ipn> [--from lib:name] [--redraw]
    symbol-draw.py <board-dir> --all [--redraw]

The second tool of process 2. A symbol either exists somewhere already and
is copied in, or it does not and is drawn from the pinout `datasheet-read`
hands back. Either way it lands in `lib/<nickname>.kicad_sym`, owned from
that point, and `parts_table` is told where it is.

    --from  copies. The nickname resolves to a KiCad library on disk
    absent  reads the datasheet and draws what it says

`source` of T1.2 takes its first letter here: `s` for a KiCad stock library,
`v` for any other library on disk, `h` for a symbol drawn against the
datasheet. The footprint letter is left as it was.

The library is merged, never rewritten. A symbol already in it is left alone
unless `--redraw` names it, because it may have been corrected by hand.

The drawing code is `build-sch.py` of proto1, which drew the archived
library.
"""

import argparse
import importlib.util
import math
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

GRID = 2.54
FONT = 1.27
PIN_LEN = 2.54
OFFSET = 1.016   # pin_names offset — the gap between the body and a name

PIN_TYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector",
    "open_emitter", "no_connect",
}
SIDES = {"L", "R", "T", "B"}

IPN = re.compile(r"^([A-Z])\d{4}$")

# Where KiCad keeps its own symbol libraries. KICAD_SYMBOL_DIR overrides.
STOCK_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/share/kicad/symbols",
]


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"symbol-draw: {message}")


def sibling(name):
    """The tool documents name the scripts with hyphens, so they are loaded
    rather than imported. The class table and the library are each written
    once and read from where they live."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ the record

def connect(board):
    path = Path(board) / "board.db"
    if not path.exists():
        raise Bad(f"{path} does not exist. Run db-init first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table", "aml_table", "mpn_table"} <= have:
        raise Bad(f"{path} is missing a table. Run db-init")
    return con


def part_row(con, ipn):
    row = con.execute(
        "select ipn, description, symbol, source from parts_table "
        "where ipn = ?", (ipn,)).fetchone()
    if row is None:
        raise Bad(f"{ipn} is not in parts_table")
    return row


def datasheet_of(con, ipn):
    row = con.execute(
        "select m.datasheet from aml_table a "
        "left join mpn_table m on m.mpn = a.mpn where a.ipn = ? "
        "order by a.rank is not null, a.rank", (ipn,)).fetchone()
    return (row[0] if row else None) or ""


def mpn_of(con, ipn):
    row = con.execute("select mpn from aml_table where ipn = ? "
                      "order by rank is not null, rank", (ipn,)).fetchone()
    return row[0] if row else ""


def write_fields(con, ipn, lib_id, letter, was_source):
    """`source` is two letters, symbol then footprint. This tool owns the
    first and does not touch the second."""
    footprint_letter = (was_source or "--")[1:2] or "-"
    con.execute("update parts_table set symbol = ?, source = ? where ipn = ?",
                (lib_id, letter + footprint_letter, ipn))
    con.commit()


# ---------------------------------------------------------------- symbols

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


# ------------------------------------------------------- reading libraries

def find_stock():
    for candidate in [os.environ.get("KICAD_SYMBOL_DIR")] + STOCK_CANDIDATES:
        if candidate and os.path.isdir(candidate):
            return Path(candidate)
    raise Bad("KiCad symbol directory not found. Set KICAD_SYMBOL_DIR")


def children(block):
    """Top-level sub-expressions of an s-expression, as (tag, text). Quoted
    strings are skipped, so a bracket inside one opens nothing."""
    out = []
    depth, in_str, esc, start = 0, False, False, None
    body = block[block.index("(") + 1:]
    for j, c in enumerate(body):
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            in_str = not in_str
        elif in_str:
            pass
        elif c == "(":
            if depth == 0:
                start = j
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                text = body[start:j + 1]
                out.append((text[1:].split(None, 1)[0].rstrip(")"), text))
            elif depth < 0:
                break
    return out


def extract_symbol(path, name):
    if not os.path.exists(path):
        return None
    src = open(path).read()
    needle = f'(symbol "{name}"'
    i = src.find(needle)
    if i < 0:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(i, len(src)):
        c = src[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            in_str = not in_str
        elif in_str:
            pass
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise Bad(f"{path}: symbol '{name}' does not close")


def flatten_extends(block, path, name):
    """KiCad stores a derived symbol with its parent's graphics folded in. A
    copy that still carries `extends` points outside the library it was
    copied into, and section 2 does not allow that."""
    m = re.search(r'\(extends "([^"]+)"\)', block)
    if not m:
        return block
    parent_name = m.group(1)
    parent = extract_symbol(path, parent_name)
    if parent is None:
        raise Bad(f"{path}: '{name}' extends '{parent_name}', which is missing")
    parent = flatten_extends(parent, path, parent_name)

    settings, props, bodies = {}, {}, []
    for who, source in (("parent", parent), ("child", block)):
        for tag, text in children(source):
            if tag == "extends":
                continue
            if tag == "property":
                props[re.match(r'\(property "([^"]*)"', text).group(1)] = text
            elif tag == "symbol":
                if who == "parent":
                    bodies.append(
                        text.replace(f'"{parent_name}_', f'"{name}_', 1))
            else:
                settings[tag] = text

    head = block[:block.index("\n") + 1]
    parts = list(settings.values()) + list(props.values()) + bodies
    return head + "".join("\t\t" + t + "\n" for t in parts) + "\t)"


def set_property(block, name, value):
    """A copied symbol keeps its drawing and takes this project's fields."""
    pattern = re.compile(r'(\(property "%s" )"[^"]*"' % re.escape(name))
    if pattern.search(block):
        return pattern.sub(lambda m: m.group(1) + '"%s"' % value, block, count=1)
    head = block[:block.index("\n") + 1]
    return head + f'\t\t(property "{name}" "{value}"\n\t\t\t(at 0 0 0)\n' \
                  f'{effects(3, hide=True)}\t\t)\n' + block[len(head):]


def rename_pins(block, rename):
    """Give a pin the name the datasheet prints. The symbol is the part; only
    the label differs, and the label is what a person reads on the sheet."""
    for number, name in (rename or {}).items():
        pattern = re.compile(
            r'(\(pin\b(?:(?!\(pin\b).)*?\(name ")[^"]*("(?:(?!\(pin\b).)*?'
            r'\(number "%s")' % re.escape(str(number)), re.S)
        block, count = pattern.subn(
            lambda m: m.group(1) + str(name) + m.group(2), block, count=1)
        if not count:
            raise Bad(f"pin {number} is not in the symbol")
    return block


def copy_symbol(source_path, source_name, ipn, prefix, description,
                datasheet, rename=None):
    block = extract_symbol(source_path, source_name)
    if block is None:
        raise Bad(f"{source_path} does not hold a symbol '{source_name}'")
    block = flatten_extends(block, source_path, source_name)
    block = block.replace(f'(symbol "{source_name}"', f'(symbol "{ipn}"', 1)
    block = block.replace(f'"{source_name}_', f'"{ipn}_')
    block = rename_pins(block, rename)
    block = set_property(block, "Reference", prefix)
    block = set_property(block, "Value", ipn)
    block = set_property(block, "Description", description or "")
    if datasheet:
        block = set_property(block, "Datasheet", datasheet)
    # The symbol is this project's from here on, and nothing in it says
    # where it came from. `origin` says it, so a copy can be read back
    # against the library it was taken from.
    block = set_property(block, "origin", f"{source_path.stem}:{source_name}")
    return "\t" + block.strip() + "\n"


# ------------------------------------------------------------- the library

def library_of(board, nickname):
    return Path(board) / "lib" / f"{nickname}.kicad_sym"


def merge(path, ipn, block, redraw):
    """Add what is missing, leave what is there — T3.1, applied to the
    library. A symbol already in it may have been corrected by hand."""
    src = path.read_text()
    present = extract_symbol(path, ipn)
    if present is not None:
        if not redraw:
            return False
        src = src.replace(present, block.strip(), 1)
        path.write_text(src)
        return True
    close = src.rstrip().rfind(")")
    if close < 0:
        raise Bad(f"{path} does not read as a kicad_symbol_lib")
    path.write_text(src[:close] + block + src[close:])
    return True


# ------------------------------------------------------------------------ run

def one(con, board, ipn, nickname, args, classes):
    ipn, description, symbol, source = part_row(con, ipn)
    lib_id = f"{nickname}:{ipn}"
    if symbol and not args.redraw:
        print(f"{ipn}  already {symbol}")
        return True

    prefix = classes[IPN.match(ipn).group(1)][1]
    datasheet = datasheet_of(con, ipn)
    datasheet_mpn = mpn_of(con, ipn)
    path = library_of(board, nickname)

    # The order of the resorts: a library that already holds the part, then
    # the datasheet. Drawing is what is done when nothing holds it.
    # The order of the resorts: a library that already holds the part, then
    # the datasheet. copy-kicad-part owns the first - it finds the symbol,
    # copies it in and renames its pins - and returns the library id it
    # wrote, or None.
    if not args.source_lib:
        hint = " ".join(filter(None, [datasheet_mpn or "", description or ""]))
        try:
            written, spec = finder.take(board, hint, ipn, nickname, args.lib)
        except SystemExit as exc:
            raise Bad(str(exc))
        if written:
            write_fields(con, ipn, written, "s", source)
            renames = spec.get("rename") or {}
            print(f"{ipn}  {written}  s  copied from {spec['library']}:"
                  f"{spec['symbol']}"
                  + (f", {len(renames)} pin(s) renamed" if renames else ""))
            return True

    lib_id_given = args.source_lib
    rename = None
    if lib_id_given:
        if ":" not in lib_id_given:
            raise Bad(f"'{lib_id_given}' is not <library>:<symbol>")
        source_nick, source_name = lib_id_given.split(":", 1)
        stock = find_stock()
        path_of_source = stock / f"{source_nick}.kicad_sym"
        letter = "s"
        if not path_of_source.exists():
            path_of_source = Path(source_nick)
            if path_of_source.suffix != ".kicad_sym":
                path_of_source = Path(f"{source_nick}.kicad_sym")
            letter = "v"
        if not path_of_source.exists():
            raise Bad(f"no library '{source_nick}' in {stock} or on disk")
        block = copy_symbol(path_of_source, source_name, ipn, prefix,
                            description, datasheet, rename)
        origin = f"copied from {source_nick}:{source_name}"
        if rename:
            origin += f", {len(rename)} pin(s) renamed"
    else:
        try:
            spec = reader.pinout(con, board, ipn, args.datasheet,
                                 args.datasheets)
        except SystemExit as exc:
            # datasheet-read raises its own class. Uncaught it would end a
            # --all pass on the first part that has nothing to read.
            raise Bad(str(exc))
        if spec is None:
            raise Bad(f"{ipn}: no pinout read. Give --from, or a datasheet")
        spec["name"] = ipn
        spec["reference"] = prefix
        spec["description"] = description or ""
        spec["datasheet"] = datasheet or spec.get("datasheet", "")
        block = build_symbol(spec)
        letter = "h"
        origin = f"drawn, {len(spec['pins'])} pins"

    changed = merge(path, ipn, block, args.redraw)
    write_fields(con, ipn, lib_id, letter, source)
    print(f"{ipn}  {lib_id}  {letter}  {origin}"
          + ("" if changed else "  (library already held it)"))
    return True


def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn", nargs="?")
    ap.add_argument("--all", action="store_true",
                    help="every part with no symbol yet")
    ap.add_argument("--from", dest="source_lib", metavar="LIB:NAME",
                    help="copy this symbol instead of drawing one")
    ap.add_argument("--nickname", help="the library nickname. Defaults to "
                                       "the .kicad_pro name")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files to search")
    ap.add_argument("--datasheet", help="the PDF, when the name does not match")
    ap.add_argument("--datasheets", help="the directory to search")
    ap.add_argument("--redraw", action="store_true",
                    help="replace a symbol the library already holds")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if bool(args.ipn) == bool(args.all):
        raise Bad("name one IPN, or --all")
    if args.ipn and not IPN.match(args.ipn):
        raise Bad(f"'{args.ipn}' is not an IPN")
    if args.all and args.source_lib:
        raise Bad("--from names one symbol, so it names one part")
    if args.all and args.datasheet:
        raise Bad("--datasheet names one file, so it names one part")

    lib_init = sibling("lib-init")
    classes = sibling("table-write").CLASSES
    global reader
    reader = sibling("datasheet-read")
    global finder
    finder = sibling("copy-kicad-part")
    try:
        nickname = lib_init.nickname_of(board, args.nickname)
        lib_init.make_library(board, nickname)
        lib_init.make_table(board, nickname)
    except SystemExit as exc:
        raise Bad(str(exc))

    con = connect(board)
    try:
        if args.ipn:
            targets = [args.ipn]
        else:
            # A part no instance puts on a page is not on a sheet, and a
            # part not on a sheet has no symbol. `sheet-place` reads a blank
            # page the same way. Asking for a symbol for a bare board is
            # asking the wrong question.
            targets = [r[0] for r in con.execute(
                "select p.ipn from parts_table p where p.symbol is null "
                "and exists (select 1 from ref_table r where r.ipn = p.ipn "
                "and r.page is not null and trim(r.page) <> '') "
                "order by p.ipn")]
            skipped = [r[0] for r in con.execute(
                "select p.ipn from parts_table p where p.symbol is null "
                "and not exists (select 1 from ref_table r where r.ipn = p.ipn "
                "and r.page is not null and trim(r.page) <> '') "
                "order by p.ipn")]
            if skipped:
                print(f"{len(skipped)} part(s) on no page, not drawn: "
                      + " ".join(skipped))
            if not targets:
                print("every part has a symbol. Nothing to draw")
                return 0

        failed = []
        for ipn in targets:
            try:
                one(con, board, ipn, nickname, args, classes)
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
