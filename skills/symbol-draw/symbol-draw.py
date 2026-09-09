#!/usr/bin/env python3
"""symbol-draw - give every part a symbol in the project library.

    symbol-draw.py <board-dir> <ipn> [--redraw] [--lib DIR ...]
    symbol-draw.py <board-dir> --all [--redraw] [--lib DIR ...]

Drawing is the last resort. For each part it runs `copy-kicad-part` first,
and only when that returns null does it draw the part file's pins
(datasheet-read.md T1). Either way `parts_table` is told where the symbol is.

Both are run as commands. Nothing is imported from another tool.

`--all` is every part whose `symbol` is null and whose instances put it on a
page. A part on no page is not on a sheet and has no symbol.

The library is merged, never rewritten: a symbol already in it is left alone
unless `--redraw` names it, because it may have been corrected by hand.

"""

import argparse
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
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
        raise Bad(f"{path} does not exist. Run init-pipeline first")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")   # off by default, per connection
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type = 'table'")}
    if not {"parts_table", "ref_table"} <= have:
        raise Bad(f"{path} is missing a table. Run init-pipeline")
    return con


def part_row(con, ipn):
    row = con.execute(
        "select ipn, description, symbol, source, name from parts_table "
        "where ipn = ? or name = ?", (ipn, ipn)).fetchone()
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
    row = con.execute("select mpn from parts_table where ipn = ?",
                      (ipn,)).fetchone()
    return (row[0] if row else None) or ""


def datasheet_of(con, ipn):
    row = con.execute("select datasheet from parts_table where ipn = ?",
                      (ipn,)).fetchone()
    return (row[0] if row else None) or ""


def sibling(name):
    import importlib.util
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), here.parent / name / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def push_symbol_fields(con, board, ipn, lib_id):
    """The fields of T2.11, written onto the just-copied or just-drawn
    symbol from the record - kicad-update owns the writer."""
    ku = sibling("kicad-update")
    nickname, name = lib_id.split(":", 1)
    library = Path(board) / "lib" / f"{nickname}.kicad_sym"
    ku.push_fields(con, library, nickname, only=name)


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
    raise Bad(f"no project library in {board}/lib. Run init-pipeline first")


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


def pin_spans(block):
    """Every pin sub-block of a symbol block, as (start, end) pairs."""
    spans = []
    for m in re.finditer(r"\n\t\t\t\(pin ", block):
        a, depth = m.start() + 1, 0
        for k in range(a, len(block)):
            if block[k] == "(":
                depth += 1
            elif block[k] == ")":
                depth -= 1
                if depth == 0:
                    spans.append((a, k + 1))
                    break
    return spans


def body_box(block):
    """The body rectangle of a symbol block, as (x1, y1, x2, y2) and the
    span it occupies. None when the symbol has no rectangle."""
    m = re.search(r"\(rectangle\s*\n\s*\(start (-?[\d.]+) (-?[\d.]+)\)"
                  r"\s*\n\s*\(end (-?[\d.]+) (-?[\d.]+)\)", block)
    if m:
        return [float(g) for g in m.groups()], m
    # A symbol drawn as polylines and arcs - an amplifier triangle, say -
    # carries no rectangle. Its body is then the extent of what is drawn,
    # and there is nothing to resize afterwards.
    pts = [(float(x), float(y))
           for x, y in re.findall(r"\(xy (-?[\d.]+) (-?[\d.]+)\)", block)]
    if not pts:
        return None
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    return [min(xs), max(ys), max(xs), min(ys)], None


def edge_of(x, y, box):
    """Which side of the body a pin enters by."""
    x1, y1, x2, y2 = box
    left, right = min(x1, x2), max(x1, x2)
    bottom, top = min(y1, y2), max(y1, y2)
    if x <= left:
        return "L"
    if x >= right:
        return "R"
    return "T" if y >= top else "B"


def tidy_pins(block, pins):
    """Two faults a donor carries into a copy, fixed on the way in.

    A part whose pins are mostly no-connect gets a body stretched by pins
    that carry nothing, so every no-connect goes to one point below the
    lower left corner. A donor that stacked pins hands three signals one
    coordinate once they are renamed, so pins sharing a point are stepped
    apart along the edge they enter by. The body is then sized to what is
    left."""
    box = body_box(block)
    if box is None:
        return block
    corners, rect = box
    spans = pin_spans(block)
    if not spans:
        return block

    dead = {str(p[0]) for p in (pins or [])
            if str(p[1]).strip().upper() in ("N/C", "NC", "NO_CONNECT")}

    read = []
    for a, b in spans:
        seg = block[a:b]
        at = re.search(r"\(at (-?[\d.]+) (-?[\d.]+) (\d+)\)", seg)
        num = re.search(r'\(number "([^"]*)"', seg)
        nm = re.search(r'\(name "([^"]*)"', seg)
        if not (at and num):
            return block
        read.append({"a": a, "b": b, "seg": seg, "num": num.group(1),
                     "name": nm.group(1) if nm else "",
                     "x": float(at.group(1)), "y": float(at.group(2)),
                     "rot": int(at.group(3))})

    for p in read:
        if not dead and p["name"].strip().upper() in ("N/C", "NC"):
            dead.add(p["num"])

    live = [p for p in read if p["num"] not in dead]
    x1, y1, x2, y2 = corners
    left, right = min(x1, x2), max(x1, x2)
    bottom, top = min(y1, y2), max(y1, y2)

    # every no-connect on one point, below the lower left corner
    for p in read:
        if p["num"] in dead:
            p["x"], p["y"], p["rot"] = left + GRID, bottom - PIN_LEN, 90

    # pins sharing a coordinate step apart along their own edge
    taken = set()
    for p in sorted(live, key=lambda q: (q["x"], q["y"])):
        p["edge"] = edge_of(p["x"], p["y"], corners)
        step = GRID if p["edge"] in ("L", "R") else GRID
        while (p["x"], p["y"]) in taken:
            if p["edge"] in ("L", "R"):
                p["y"] = snap(p["y"] - step)
            else:
                p["x"] = snap(p["x"] + step)
        taken.add((p["x"], p["y"]))

    # the body holds what is left
    if live:
        ys = [p["y"] for p in live if p["edge"] in ("L", "R")]
        xs = [p["x"] for p in live if p["edge"] in ("T", "B")]
        if ys:
            bottom = min(bottom, min(ys) - GRID)
            top = max(top, max(ys) + GRID)
        if xs:
            left = min(left, min(xs) - GRID)
            right = max(right, max(xs) + GRID)

    out, last = [], 0
    for p in sorted(read, key=lambda q: q["a"]):
        seg = re.sub(r"\(at -?[\d.]+ -?[\d.]+ \d+\)",
                     f"(at {p['x']} {p['y']} {p['rot']})", p["seg"], count=1)
        out.append(block[last:p["a"]])
        out.append(seg)
        last = p["b"]
    out.append(block[last:])
    done = "".join(out)

    if rect is None:
        return done
    # The pins were rewritten above, so the rectangle's offsets in the
    # original block no longer hold. Find it again in what we have now.
    again = re.search(r"\(rectangle\s*\n\s*\(start -?[\d.]+ -?[\d.]+\)"
                      r"\s*\n\s*\(end -?[\d.]+ -?[\d.]+\)", done)
    if not again:
        return done
    new_rect = (f"(rectangle\n\t\t\t\t(start {left} {top})"
                f"\n\t\t\t\t(end {right} {bottom})")
    return done[:again.start()] + new_rect + done[again.end():]


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

def try_copy(board, ipn, hint, extra, pins=None, take=None):
    """`copy-kicad-part`, run as a command. Returns the library id it wrote,
    or None. `pins` is the datasheet pinout; the copy renames by number
    from it and refuses a count mismatch."""
    argv = [sys.executable, str(HERE.parent / "copy-kicad-part" / "copy-kicad-part.py"), str(board),
            hint, "--ipn", ipn]
    if take:
        argv += ["--take", take]
    handle = None
    if pins:
        import tempfile, os as _os
        handle, path = tempfile.mkstemp(suffix=".json")
        _os.close(handle)
        Path(path).write_text(json.dumps(pins))
        argv += ["--pinout", path]
    for folder in extra or []:
        argv += ["--lib", folder]
    run = subprocess.run(argv, capture_output=True, text=True)
    if handle is not None:
        Path(path).unlink(missing_ok=True)
    for line in run.stdout.splitlines():
        if line.startswith("kpi "):
            print(f"    {line}")
    if run.returncode != 0:
        raise Bad((run.stderr or run.stdout).strip())
    # Without --take, copy-kicad-part prints a shortlist and stops - the
    # choice belongs to the running session (its doc, Show choose take).
    # This automatic resort then has no answer: report and move on.
    if "rerun with --take" in run.stdout:
        return None, run.stdout
    # copy-kicad-part prints `<name>  <library:id>` or `<name>  null`
    first = (run.stdout.strip().splitlines() or ["x null"])[0]
    tokens = first.split()
    if len(tokens) < 2 or tokens[1] == "null":
        return None, run.stdout
    return tokens[1], run.stdout


def try_read(board, ipn, pname):
    """The part file, `design/parts/<IPN>-<name>.json` (datasheet-read.md
    T1). Returns its `pins`, or None when the part has no file. Nothing
    is read from a datasheet here - that is the running session's work."""
    part = Path(board) / "parts" / f"{ipn}-{pname or ipn}.json"
    if not part.is_file():
        print("    no part file")
        return None
    pins = json.load(open(part)).get("pins")
    print(f"    part file  {len(pins or [])} pins")
    return pins


def tidy_in_library(library, name, pins):
    """Tidy a symbol already written into the project library. Returns True
    when the block changed."""
    src = library.read_text()
    needle = f'(symbol "{name}"'
    start = src.find(needle)
    if start < 0:
        return False
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    else:
        return False
    block = src[start:end]
    fixed = tidy_pins(block, pins)
    if fixed == block:
        return False
    library.write_text(src[:start] + fixed + src[end:])
    return True


def one(con, board, ipn, nickname, library, args):
    ipn, description, symbol, source, pname = part_row(con, ipn)
    label = pname or ipn
    if symbol and not args.redraw:
        print(f"{ipn}  already {symbol}")
        return True

    mpn = mpn_of(con, ipn)
    hint = " ".join(filter(None, [mpn, description]))

    # Gather before picking - n9_1.29. The pinout is read first; the pick
    # runs knowing the part's pin count. No pinout, no count - the pick
    # still runs, held to the guidelines alone.
    pins = try_read(board, ipn, pname)

    take = getattr(args, "source_lib", None)
    if take:
        lib_part, _, sym_part = take.partition(":")
        if not sym_part:
            raise Bad("--from is LIB:NAME")
        extra = list(args.lib or [])
        letter = "s"
        donor = Path(lib_part)
        if donor.suffix == ".kicad_sym":
            if not donor.exists():
                raise Bad(f"--from: {donor} is not on disk")
            extra.append(str(donor.parent))
            take = f"{donor.stem}:{sym_part}"
            letter = "v"
        written, out = try_copy(board, label, hint, extra, pins, take)
        if not written:
            raise Bad(f"{ipn}: --from refused - "
                      + out.strip().replace("\n", "; "))
        tidy_in_library(library, written.split(":", 1)[-1], pins)
        write_fields(con, ipn, written, letter, source)
        push_symbol_fields(con, board, ipn, written)
        print(f"{ipn}  {written}  {letter}  copied from {take}"
              + (f", pinout known, {len(pins)} pins" if pins else ""))
        return True

    written, _ = (None, "") if args.draw else try_copy(board, label, hint,
                                                       args.lib, pins)
    if written:
        tidy_in_library(library, written.split(":", 1)[-1], pins)
        write_fields(con, ipn, written, "s", source)
        push_symbol_fields(con, board, ipn, written)
        print(f"{ipn}  {written}  s  copied"
              + (f", pinout known, {len(pins)} pins" if pins else ""))
        return True

    if args.copy_only:
        raise Bad(f"{ipn}: no library holds it - secondary (draw) awaits "
                  f"the User's word")

    if not pins:
        raise Bad(f"{ipn}: no library holds it and no pinout could be read")

    spec = {"name": label, "reference": prefix_of(con, ipn), "pins": pins,
            "description": description or "",
            "datasheet": datasheet_of(con, ipn)}
    merge(library, label, tidy_pins(build_symbol(spec), pins), True)
    write_fields(con, ipn, f"{nickname}:{label}", "h", source)
    push_symbol_fields(con, board, ipn, f"{nickname}:{label}")
    print(f"{ipn}  {nickname}:{label}  h  drawn, {len(pins)} pins")
    return True


# ------------------------------------------------------------------------ run

def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("ipn", nargs="?")
    ap.add_argument("--copy-only", action="store_true",
                    help="stage primary pass: a copy miss is reported, "
                         "never drawn (T4.1 - secondary needs the User)")
    ap.add_argument("--all", action="store_true",
                    help="every part on a page with no symbol yet")
    ap.add_argument("--redraw", action="store_true",
                    help="replace a symbol the library already holds")
    ap.add_argument("--draw", action="store_true",
                    help="skip the copy resort - draw from the pinout")
    ap.add_argument("--from", dest="source_lib", metavar="LIB:NAME",
                    help="copy this donor - a stock nickname, or a "
                         ".kicad_sym path, colon, the symbol name")
    ap.add_argument("--lib", action="append",
                    help="another directory of .kicad_sym files. Repeatable")
    ap.add_argument("--datasheets", help="the directory of datasheets")
    args = ap.parse_args(argv[1:])

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")
    if bool(args.ipn) == bool(args.all):
        raise Bad("name one IPN, or --all")
    if args.all and args.source_lib:
        raise Bad("--all with --from, which names one symbol and so "
                  "names one part")
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
        if args.all and len(targets) > 1:
            # n5.17: the parts share nothing - a batch of worker
            # subprocesses, each part's output printed whole as it lands,
            # with a progress line: timestamp, x of y, elapsed, remaining,
            # ETA.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from datetime import datetime, timedelta

            def run_one(ipn):
                argv = [sys.executable, __file__, str(board), ipn]
                if args.copy_only:
                    argv.append("--copy-only")
                if args.redraw:
                    argv.append("--redraw")
                for folder in args.lib or []:
                    argv += ["--lib", folder]
                return ipn, subprocess.run(argv, capture_output=True,
                                           text=True)

            start = time.time()
            done = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(run_one, ipn) for ipn in targets]
                for future in as_completed(futures):
                    ipn, run = future.result()
                    done += 1
                    sys.stdout.write(run.stdout)
                    if run.returncode != 0:
                        sys.stdout.write(run.stderr)
                        failed.append(ipn)
                    elapsed = time.time() - start
                    remaining = (len(targets) - done) * elapsed / done
                    eta = datetime.now() + timedelta(seconds=remaining)
                    print(f"{datetime.now():%H:%M:%S}  {done} of "
                          f"{len(targets)}  elapsed {elapsed:.0f}s  "
                          f"remaining ~{remaining:.0f}s  ETA {eta:%H:%M:%S}",
                          flush=True)
            failed.sort()
        else:
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
