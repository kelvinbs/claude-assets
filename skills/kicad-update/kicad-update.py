#!/usr/bin/env python3
"""kicad-update — carry the record onto the pages, and the pages back.

    kicad-update.py <board-dir> [--assign <uuid>=<ipn> ...]

The skill of stages 4 and 6. Every instance in `ref_table` that carries a
page and whose part carries a symbol is drawn on that page, in the order of
T2.12: room first, then family for the instances with no room.

The return direction, section 2.2: every page is read back. A symbol the
User placed enters `ref_table` under its own uuid when its `ipn` field names
a part, or when `--assign` names one for it. A field the sheet holds
differently from the record is rewritten from the record. The tool deletes
on neither side.

It adds what is missing and leaves what is there. A symbol already on a page
keeps its position, and every wire, label and graphic on that page is left
untouched — the page is edited, not rewritten. The User wires the sheet, and
a rerun must not undo it.

The instance UUID of `ref_table` is the symbol's UUID in the sheet. That is
what makes a part on a page the same part as the row, run after run, and
what `board-place` follows to the footprint.

The sheet writing is `build-sch.py` of proto1.
"""

import argparse
import importlib.util
import json
import math
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent

SCH_VERSION = 20250114
GRID = 2.54
FONT = 1.27
LINE = 2.54   # field line pitch; Reference over Value at the lower right

# A fixed namespace, so a rerun that changes nothing produces no diff.
NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# ANSI sheet sizes, landscape, in millimetres. KiCad names them by letter.
PAPERS = {
    "A": (279.4, 215.9),
    "B": (431.8, 279.4),
    "C": (558.8, 431.8),
    "D": (863.6, 558.8),
    "E": (1117.6, 863.6),
    # ISO sizes appear on sheets other tools made (init-pipeline writes
    # A4). An existing page keeps its size; only new pages take ANSI.
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}
PAPER_ORDER = ["A", "B", "C", "D", "E"]

IPN = re.compile(r"^([A-Z])\d{4}$")


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"kicad-update: {message}")


def sibling(name):
    """The library reader and the class table are each written once, in the
    tool that owns them, and loaded from there."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), HERE.parent / name / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


copy_part = sibling("copy-kicad-part")


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


def snap(v):
    return round(v / GRID) * GRID


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


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


def value_of(con, ipn, description):
    """T2.11: the blank-rank MPN, else the description, else the IPN -
    Value is what a person reads on a sheet."""
    mpn = con.execute("select mpn from parts_table where ipn = ?",
                      (ipn,)).fetchone()
    return (mpn and mpn[0]) or description or ipn


def instances(con):
    """One row per thing to draw, with everything the sheet needs on it.

    `Value` shows the part number the board was designed against — the
    blank-rank row of T2.6 — and falls back to the description for a part
    that has no part number yet. The IPN is on the row either way, in its
    own field, and that is what the record keys on."""
    rows = []
    for uuid_, ipn, ref, page, room, unit, symbol, footprint, description, \
            datasheet, manufacturer, mpn, note, checked in con.execute(
            "select r.uuid, r.ipn, r.ref, r.page, r.room, r.unit, "
            "       p.symbol, p.footprint, p.description, p.datasheet, "
            "       p.manufacturer, p.mpn, p.note, p.checked "
            "from ref_table r join parts_table p on p.ipn = r.ipn "
            "order by r.ipn, r.ref, r.unit"):
        rows.append({
            "uuid": uuid_, "ipn": ipn, "ref": ref or "",
            "page": (page or "").strip(), "room": (room or "").strip(),
            "unit": unit or 1,
            "symbol": symbol, "footprint": footprint or "",
            "value": value_of(con, ipn, description),
            "description": description or "", "datasheet": datasheet or "",
            "manufacturer": manufacturer or "", "mpn": mpn or "",
            "note": note or "", "checked": checked or "no",
            "parent": "", "room_field": (room or "").strip(),
        })
    # T2.11: `parent` reaches the sheet as the parent instance's reference,
    # not its uuid. A reference is what an engineer reads on a page
    ref_of_uuid = {r["uuid"]: r["ref"] for r in rows}
    parent_of = dict(con.execute(
        "select uuid, parent from ref_table where parent is not null"))
    for r in rows:
        r["parent"] = ref_of_uuid.get(parent_of.get(r["uuid"]), "")
    return rows


def number_of(ref):
    m = re.match(r"^([A-Za-z]+)(\d+)$", ref or "")
    return int(m.group(2)) if m else 0


def order_of(row):
    """T2.12, ranks 2 and 3. Rooms come first, in name order. A child sorts
    under its parent, so a parent and the parts that serve it stay together.
    What has neither runs by reference."""
    number = number_of(row["ref"])
    unit = row.get("unit") or 1
    parent = row.get("parent") or ""
    if row["room"]:
        return (0, row["room"], parent, number_of(parent), number, unit)
    if parent:
        return (1, "", parent, number_of(parent), number, unit)
    return (1, "", row["ref"], number, number, unit)


# ------------------------------------------------------------------ the symbol

def property_sexp(name, value, x, y, hide=False, justify=None):
    hidden = "\t\t\t\t(hide yes)\n" if hide else ""
    just = f"\t\t\t\t(justify {justify})\n" if justify else ""
    return (
        f"\t\t(property \"{name}\" \"{value}\"\n"
        f"\t\t\t(at {x} {y} 0)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size {FONT} {FONT})\n"
        f"\t\t\t\t)\n{just}{hidden}\t\t\t)\n"
        f"\t\t)\n"
    )


def instance_sexp(project, path_uuid, row, x, y, bottom, right):
    unit = row.get("unit") or 1
    first = unit == 1
    return (
        "\t(symbol\n"
        f"\t\t(lib_id \"{row['symbol']}\")\n"
        f"\t\t(at {x:.2f} {y:.2f} 0)\n"
        f"\t\t(unit {unit})\n"
        "\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
        "\t\t(dnp no)\n\t\t(fields_autoplaced yes)\n"
        f"\t\t(uuid \"{row['uuid']}\")\n"
        + property_sexp("Reference", row["ref"], f"{x + right:.2f}",
                        f"{y + bottom + LINE:.2f}", justify="left")
        + property_sexp("Value", row["value"], f"{x + right:.2f}",
                        f"{y + bottom + 2 * LINE:.2f}", justify="left",
                        hide=not first)
        + property_sexp("Footprint", row["footprint"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("Datasheet", row["datasheet"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("Description", row["description"], f"{x:.2f}",
                        f"{y:.2f}", hide=True)
        + property_sexp("Manufacturer", row["manufacturer"], f"{x:.2f}",
                        f"{y:.2f}", hide=True)
        + property_sexp("MPN", row["mpn"], f"{x:.2f}", f"{y:.2f}", hide=True)
        + property_sexp("note", row["note"], f"{x:.2f}", f"{y:.2f}", hide=True)
        + property_sexp("ipn", row["ipn"], f"{x:.2f}", f"{y:.2f}", hide=True)
        + property_sexp("parent", row["parent"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("room", row["room_field"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("checked", row["checked"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + "\t\t(instances\n"
        f"\t\t\t(project \"{project}\"\n"
        f"\t\t\t\t(path \"{path_uuid}\"\n"
        f"\t\t\t\t\t(reference \"{row['ref']}\")\n\t\t\t\t\t(unit {unit})\n"
        "\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        "\t)\n"
    )


def sheet_sexp(project, root, name, filename, page_number, x, y, w, h):
    return (
        "\t(sheet\n"
        f"\t\t(at {x:.2f} {y:.2f})\n"
        f"\t\t(size {w:.2f} {h:.2f})\n"
        "\t\t(fields_autoplaced yes)\n"
        "\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n"
        "\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n"
        f"\t\t(uuid \"{uid(project, 'sheet', name)}\")\n"
        + property_sexp("Sheetname", name, f"{x:.2f}", f"{y - GRID:.2f}")
        + property_sexp("Sheetfile", filename, f"{x:.2f}",
                        f"{y + h + GRID:.2f}")
        + "\t\t(instances\n"
        f"\t\t\t(project \"{project}\"\n"
        f"\t\t\t\t(path \"/{root}\"\n\t\t\t\t\t(page \"{page_number}\")\n"
        "\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        "\t)\n"
    )


# ---------------------------------------------------------------- the library

def unit_count(block, name):
    """How many units the symbol has - the greatest x in its NAME_x_y
    children. 0 and shared children do not add units."""
    return max([int(m) for m in
                re.findall(r'\(symbol "%s_(\d+)_\d+"' % re.escape(name),
                           block)] or [1])


def library_blocks(board, nickname, lib_ids):
    """The symbol definitions a page has to carry, keyed by lib_id. KiCad
    keeps a copy in the sheet; it is compared against the library on open."""
    out = {}
    for lib_id in sorted(lib_ids):
        nick, name = lib_id.split(":", 1)
        if nick != nickname:
            raise Bad(f"{lib_id} is not in this project's library. "
                      f"symbol-draw copies a symbol in before it is placed")
        path = Path(board) / "lib" / f"{nick}.kicad_sym"
        src = path.read_text(errors="replace")
        block = copy_part.top_level(src, name)
        if block is None:
            raise Bad(f"{path} does not hold '{name}'. Run symbol-draw")
        block = copy_part.flatten(block, src, name)
        out[lib_id] = block.replace(f'(symbol "{name}"',
                                    f'(symbol "{lib_id}"', 1)
    return out


def drawing(block):
    """The block with everything that draws nothing removed: the library's
    own field positions (n9.7 — they sat far out and pushed the fields
    away) and polylines whose points all coincide."""
    body = re.sub(r'\n\s*\(property "[^"]*" "(?:[^"\\]|\\.)*"\n(?:.*?\n)*?\s*\)',
                  "", block)
    for m in re.finditer(r'\(polyline\n(?:.*?\n)*?\s*\(pts\n((?:.*?\n)*?)\s*\)', body):
        pts = set(re.findall(r'\(xy (-?[\d.]+) (-?[\d.]+)\)', m.group(1)))
        if len(pts) < 2:
            body = body.replace(m.group(0), "", 1)
    return body


COORD = r'\((?:start|end|xy|at|center) (-?[\d.]+) (-?[\d.]+)'


def extent(block):
    """Half-width and half-height of what is drawn — pins and graphics —
    for the spacing between parts, so pins never overlap (n6.7)."""
    xs, ys = [], []
    for x, y in re.findall(COORD, drawing(block)):
        xs.append(abs(float(x)))
        ys.append(abs(float(y)))
    return (max(xs) if xs else GRID), (max(ys) if ys else GRID)


def edges(block):
    """How far the drawing reaches down and to the right of the origin,
    each side on its own (n9_1.6, n9_1.19). The field pair hangs off the
    lower right corner. Pins count: a field over a pin stub is
    unreadable."""
    pts = [(float(x), float(y))
           for x, y in re.findall(COORD, drawing(block))]
    if not pts:
        return GRID, GRID
    bottom = max(-min(y for _, y in pts), 0.0)
    right = max(max(x for x, _ in pts), 0.0)
    return bottom, right


def indent_block(block, tabs):
    pad = "\t" * tabs
    return "\n".join(pad + ln if ln.strip() else ln for ln in block.split("\n"))


# ------------------------------------------------------------- reading back

def esc(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def unesc(value):
    return value.replace('\\"', '"').replace("\\\\", "\\")


def symbol_blocks(src):
    """Every placed symbol on a page — the top-level `(symbol` blocks, as
    (start, end) spans of the text. Library definitions sit one level down
    inside `lib_symbols` and are not matched."""
    spans, i = [], 0
    while True:
        j = src.find("\n\t(symbol\n", i)
        if j < 0:
            return spans
        start = j + 1
        depth, k, in_str = 0, start, False
        while k < len(src):
            c = src[k]
            if in_str:
                if c == "\\":
                    k += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        spans.append((start, k))
        i = k


PROP = r'\n\t\t\(property "%s" "((?:[^"\\]|\\.)*)"'


def read_symbol(block):
    """What the sheet says about one placed symbol: its uuid, its lib_id and
    the four fields of T2.11."""
    u = re.search(r'\n\t\t\(uuid "([^"]+)"\)', block)
    lib = re.search(r'\(lib_id "([^"]+)"\)', block)
    unit = re.search(r'\n\t\t\(unit (\d+)\)', block)
    props = {name: unesc(val) for name, val in re.findall(
        r'\n\t\t\(property "([^"]+)" "((?:[^"\\]|\\.)*)"', block)}
    return {"uuid": u.group(1) if u else None,
            "lib_id": lib.group(1) if lib else "",
            "unit": int(unit.group(1)) if unit else 1,
            "props": props}


def set_property(block, name, value):
    """Rewrite one field's value in a symbol block, adding the field, hidden,
    at the symbol's own position when the block does not carry it."""
    pat = re.compile(PROP % re.escape(name))
    if pat.search(block):
        return pat.sub(lambda m: m.group(0)[:m.start(1) - m.start(0)]
                       + esc(value) + '"', block, count=1)
    at = re.search(r'\n\t\t\(at (-?[\d.]+) (-?[\d.]+)', block)
    x, y = (at.group(1), at.group(2)) if at else ("0", "0")
    prop = property_sexp(name, esc(value), x, y, hide=True)
    spots = [i for i in (block.find("\n\t\t(pin "),
                         block.find("\n\t\t(instances"),
                         block.find('\n\t\t(symbol "')) if i >= 0]
    anchor = min(spots) if spots else block.rstrip().rfind("\n")
    return block[:anchor + 1] + prop + block[anchor + 1:]


def set_reference(block, ref):
    block = set_property(block, "Reference", ref)
    return re.sub(r'\(reference "[^"]*"\)', f'(reference "{esc(ref)}")',
                  block)


def page_names(root_src):
    """Sheetfile to Sheetname, from the root's sheet symbols."""
    names = re.findall(r'\(property "Sheetname" "([^"]*)"', root_src)
    files = re.findall(r'\(property "Sheetfile" "([^"]*)"', root_src)
    return dict(zip(files, names))


def read_pages(board, project, root_src):
    """Every symbol on every page: uuid -> (file name, page name, symbol,
    block span). Pages are the files the root names, plus any
    `<project>-*.kicad_sch` beside them."""
    by_file = page_names(root_src)
    found = {}
    for path in sorted(board.glob(f"{project}-*.kicad_sch")):
        page = by_file.get(path.name)
        if page is None:
            continue
        src = path.read_text()
        for start, end in symbol_blocks(src):
            sym = read_symbol(src[start:end])
            if sym["uuid"]:
                found[sym["uuid"]] = (path.name, page, sym)
    return found


def next_ref_free(con, prefix, taken):
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


# ------------------------------------------------- the library fields (T2.11)

FIELDS = ["Value", "Footprint", "Description", "Datasheet", "Manufacturer",
          "MPN", "note", "ipn", "checked"]

PULLED = {"Description": ("parts_table", "description"),
          "Footprint": ("parts_table", "footprint"),
          "note": ("parts_table", "note"),
          "Manufacturer": ("parts_table", "manufacturer"),
          "Datasheet": ("parts_table", "datasheet")}


def field_rows(con, nickname):
    """One dict of T2.11 fields per part whose symbol is in this project's
    library, keyed by the symbol name."""
    out = {}
    for ipn, description, footprint, note, symbol, mpn, manufacturer, \
            datasheet, checked in con.execute(
            "select ipn, description, footprint, note, symbol, mpn, "
            "manufacturer, datasheet, checked from parts_table "
            "where symbol is not null"):
        nick, _, name = symbol.partition(":")
        if nick != nickname:
            continue
        out[name] = {"ipn": ipn,
                     "Value": mpn or description or ipn,
                     "Footprint": footprint or "",
                     "Description": description or "",
                     "Datasheet": datasheet or "",
                     "Manufacturer": manufacturer or "",
                     "MPN": mpn or "",
                     "checked": checked or "no",
                     "note": note or ""}
    return out


def lib_blocks(src):
    """The library's top-level symbols: name -> (start, end)."""
    out, i = {}, 0
    while True:
        j = src.find('\n\t(symbol "', i)
        if j < 0:
            return out
        start = j + 1
        name = re.match(r'\t\(symbol "([^"]+)"', src[start:]).group(1)
        depth, k, in_str = 0, start, False
        while k < len(src):
            c = src[k]
            if in_str:
                if c == "\\":
                    k += 1
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    k += 1
                    break
            k += 1
        out[name] = (start, k)
        i = k


def normalize_lib(path):
    run = subprocess.run(["kicad-cli", "sym", "upgrade", "--force",
                          str(path)], capture_output=True, text=True)
    if run.returncode != 0:
        raise Bad(f"kicad-cli could not normalize {path}: "
                  + (run.stderr or run.stdout).strip())


def push_fields(con, library, nickname, only=None):
    """Record to library: rewrite every symbol's T2.11 fields. Graphics are
    not touched. Returns the names whose text changed."""
    before = src = library.read_text()
    want = field_rows(con, nickname)
    changed = []
    # Highest span first: an edit changes the offsets of everything after
    # it, so the blocks still untouched must all sit before the edit.
    for name, (a, b) in sorted(lib_blocks(src).items(),
                               key=lambda kv: -kv[1][0]):
        if name not in want or (only and name != only):
            continue
        block = new = src[a:b]
        for field in FIELDS:
            new = set_property(new, field, want[name][field])
        if new != block:
            src = src[:a] + new + src[b:]
            changed.append(name)
    if changed:
        library.write_text(src)
        try:
            normalize_lib(library)
        except BaseException:
            library.write_text(before)
            raise
    return changed


INSTANCE_FIELDS = (("Value", "value"), ("Footprint", "footprint"),
                   ("Description", "description"), ("Datasheet", "datasheet"),
                   ("Manufacturer", "manufacturer"), ("MPN", "mpn"),
                   ("note", "note"), ("parent", "parent"),
                   ("room", "room_field"), ("checked", "checked"))


def push_instances(con, board, project, root_src):
    """Record to sheets: T2.11's fields onto every placed instance the
    record knows (n4.3). Positions, wiring and graphics untouched. Returns
    {sheet file: instances rewritten}."""
    rows = {r["uuid"]: r for r in instances(con)}
    placed = read_pages(board, project, root_src)
    by_file = {}
    for u, (fname, page, sym) in placed.items():
        if u in rows:
            by_file.setdefault(fname, set()).add(u)
    changed = {}
    for fname, wanted in sorted(by_file.items()):
        path = board / fname
        before = src = path.read_text()
        out, last, count = [], 0, 0
        for start, end in symbol_blocks(src):
            block = src[start:end]
            u = read_symbol(block)["uuid"]
            if u not in wanted:
                continue
            new = block
            for field, key in INSTANCE_FIELDS:
                new = set_property(new, field, rows[u][key])
            if new != block:
                out.append(src[last:start]); out.append(new); last = end
                count += 1
        if count:
            out.append(src[last:])
            path.write_text("".join(out))
            run = subprocess.run(["kicad-cli", "sch", "upgrade", "--force",
                                  str(path)], capture_output=True, text=True)
            if run.returncode != 0:
                path.write_text(before)
                raise Bad(f"kicad-cli could not normalize {path}: "
                          + (run.stderr or run.stdout).strip())
            changed[fname] = count
    return changed


def pull_fields(con, library, nickname):
    """Library to record: Description, Footprint, note to `parts_table`;
    Manufacturer, Datasheet to the blank-rank MPN. MPN and Value are
    reported on mismatch, never written."""
    src = library.read_text()
    want = field_rows(con, nickname)
    blocks = lib_blocks(src)
    applied, reported = [], []
    for name, fields in sorted(want.items()):
        if name not in blocks:
            continue
        a, b = blocks[name]
        props = read_symbol(src[a:b])["props"]
        ipn = fields["ipn"]
        if props.get("ipn", "").strip() != ipn:
            reported.append((ipn, "ipn", ipn, props.get("ipn", "")))
            continue
        for field in FIELDS:
            if field == "ipn" or field not in props:
                continue
            have, held = props[field].strip(), fields[field]
            if have == held:
                continue
            table, column = PULLED.get(field, (None, None))
            if table == "parts_table":
                con.execute(f"update parts_table set {column} = ? "
                            "where ipn = ?", (have or None, ipn))
                applied.append((ipn, field, held, have))
            else:
                reported.append((ipn, field, held, have))
    con.commit()
    return applied, reported


# ---------------------------------------------------------------- the placing

MARGIN = 10 * GRID      # clear of the sheet frame, which sits ~10 mm in
GAP = 5 * GRID          # between parts inside a box
BOX_GAP = 12 * GRID     # between boxes on the page


def size_of(row, blocks):
    """The space one symbol needs, its own drawing plus the clearance that
    keeps a neighbour's pins off it."""
    half_w, half_h = extent(blocks[row["symbol"]])
    return 2 * half_w, 2 * (half_h + GRID)


def shelf(items, limit, gap=GAP, down=False):
    """Pack items along one axis, wrapping at `limit`. An item is
    (w, h, payload). With `down` the axes swap: items stack downward and a
    new column starts at the limit. Returns [(x, y, payload)] and the
    packed size, both in page axes either way."""
    if down:
        items = [(h, w, payload) for w, h, payload in items]
    placed, cur_x, cur_y, row_h, used_w = [], 0.0, 0.0, 0.0, 0.0
    for w, h, payload in items:
        if cur_x + w > limit and cur_x > 0:
            cur_x, cur_y, row_h = 0.0, snap(cur_y + row_h + gap), 0.0
        placed.append((cur_x, cur_y, payload))
        cur_x = snap(cur_x + w + gap)
        row_h = max(row_h, h)
        used_w = max(used_w, cur_x - gap)
    if down:
        return ([(y, x, payload) for x, y, payload in placed],
                cur_y + row_h, used_w)
    return placed, used_w, cur_y + row_h


ASPECT = 3.0            # a box comes out this many times wider than tall


def box_extent(items, down=False):
    """The limit that makes a box come out ASPECT times wider than tall: the
    area its contents take, shaped to that ratio. Packing down the limit is
    a height, so the ratio inverts. Never smaller than the largest item on
    the axis being wrapped."""
    if not items:
        return GRID
    area = sum((w + GAP) * (h + GAP) for w, h, _ in items)
    if down:
        return max(max(h for _, h, _ in items), math.sqrt(area / ASPECT))
    return max(max(w for w, _, _ in items), math.sqrt(area * ASPECT))


def boxes_of(rows, blocks):
    """A box is a parent and everything under it, else a room, else the part
    on its own. A child that is itself a parent packs first and enters its
    parent's box as one item, so nesting needs no special case."""
    by_ref = {}
    for row in rows:
        by_ref.setdefault(row["ref"], []).append(row)
    parent_of = {ref: (rs[0].get("parent") or "") for ref, rs in by_ref.items()}
    # A parent that draws nothing - a functional block, whose part carries no
    # symbol - never reaches this function as a row. It is still a box: it
    # holds no drawing of its own and its children pack inside it.
    for par in list(parent_of.values()):
        if par and par not in by_ref:
            by_ref.setdefault(par, [])
            parent_of.setdefault(par, "")
    kids = {}
    for ref, par in parent_of.items():
        if par and par in by_ref:
            kids.setdefault(par, []).append(ref)

    def pack(ref):
        """(width, height, [(dx, dy, row)]) for this reference and its
        descendants, laid out inside a box of its own."""
        items = []
        for row in sorted(by_ref[ref], key=lambda r: r.get("unit") or 1):
            w, h = size_of(row, blocks)
            items.append((w, h, ("part", row)))
        for kid in sorted(kids.get(ref, []), key=number_of):
            kw, kh, inner = pack(kid)
            items.append((kw, kh, ("box", inner)))
        placed, w, h = shelf(items, box_extent(items, down=True),
                             down=True)
        flat = []
        for x, y, (kind, payload) in placed:
            if kind == "part":
                flat.append((x, y, payload))
            else:
                flat.extend((x + dx, y + dy, r) for dx, dy, r in payload)
        return w, h, flat

    tops = [ref for ref, par in parent_of.items()
            if not par or par not in by_ref]

    def rank(ref):
        rs = by_ref[ref] or [by_ref[k][0] for k in kids.get(ref, [])
                             if by_ref.get(k)]
        return order_of(rs[0]) if rs else (1, "", ref, 0, 0, 0)

    out = []
    for ref in sorted(tops, key=rank):
        w, h, flat = pack(ref)
        if flat:
            out.append((w, h, flat))
    return out


def outline_sexp(drawn):
    """A thin rectangle round a box, so a parent and what serves it read as
    one thing on the page."""
    pad = 1.5 * GRID
    x0 = min(x - hw for x, _, hw, _ in drawn) - pad
    x1 = max(x + hw for x, _, hw, _ in drawn) + pad
    y0 = min(y - hh for _, y, _, hh in drawn) - pad
    y1 = max(y + hh for _, y, _, hh in drawn) + pad
    return (
        "\t(rectangle\n"
        f"\t\t(start {x0:.2f} {y0:.2f})\n"
        f"\t\t(end {x1:.2f} {y1:.2f})\n"
        "\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)\n"
        "\t\t(fill\n\t\t\t(type none)\n\t\t)\n"
        f"\t\t(uuid \"{uuid.uuid4()}\")\n"
        "\t)\n"
    )


def flow(rows, blocks, project, path_uuid, width, start_y, height=None):
    """Lay the sheet as boxes, not as text. A box is a parent and everything
    under it, packed roughly square and sized by its contents. Boxes then
    pack the page, largest first, and a box never splits across a wrap."""
    boxes = boxes_of(rows, blocks)
    boxes.sort(key=lambda b: -(b[0] * b[1]))
    items = [(w, h, flat) for w, h, flat in boxes]
    room = (height or width) - start_y - MARGIN
    placed, _, used_h = shelf(items, max(room, GRID), gap=BOX_GAP, down=True)

    body, page_h, page_w = "", start_y, MARGIN
    for bx, by, flat in placed:
        drawn, refs = [], set()
        for dx, dy, row in flat:
            half_w, half_h = extent(blocks[row["symbol"]])
            bottom, right = edges(blocks[row["symbol"]])
            x = snap(MARGIN + bx + dx + half_w)
            y = snap(start_y + by + dy + half_h + GRID)
            page_h = max(page_h, y + half_h + GRID + MARGIN)
            page_w = max(page_w, x + half_w + GRID + MARGIN)
            body += instance_sexp(project, path_uuid, row, x, y, bottom, right)
            drawn.append((x, y, half_w, half_h))
            refs.add(row["ref"])
        if len(refs) > 1:
            body += outline_sexp(drawn)
    return body, page_h, page_w


def fit_paper(lay, fixed):
    """Lay the page out on each sheet size in turn and take the first it
    fits."""
    sizes = [fixed] if fixed else PAPER_ORDER
    for name in sizes:
        width, height = PAPERS[name]
        drawn, tall, wide = lay(width, height)
        if (tall <= height and wide <= width) or name == sizes[-1]:
            return name, drawn
    raise Bad("no sheet size fits")


# ----------------------------------------------------------------- the sheets

def existing_uuids(src):
    return set(re.findall(r'\(uuid "([0-9a-fA-F-]{36})"\)', src))


def paper_of(src):
    m = re.search(r'\(paper "([^"]*)"\)', src)
    return m.group(1) if m else "A"


def lowest_used(src):
    ys = [float(y) for _, y in
          re.findall(r'\(at (-?[\d.]+) (-?[\d.]+)(?: 0)?\)', src)]
    return max(ys) if ys else 0.0


def new_sheet(project, file_uuid, paper, lib_symbols, body):
    return (
        "(kicad_sch\n"
        f"\t(version {SCH_VERSION})\n"
        "\t(generator \"kicad-update.py\")\n"
        "\t(generator_version \"10.0\")\n"
        f"\t(uuid \"{file_uuid}\")\n"
        f"\t(paper \"{paper}\")\n"
        f"\t(lib_symbols\n{lib_symbols}\t)\n"
        f"{body}"
        "\t(embedded_fonts no)\n"
        ")\n"
    )


def merge_sheet(src, blocks, needed, body):
    """Add symbols to a page that is already drawn on. The file is opened,
    added to, and closed — everything the User put there stays where it is."""
    have = set(re.findall(r'\t\t\(symbol "([^"]+)"', src))
    missing = "".join(indent_block(blocks[k], 1).rstrip() + "\n"
                      for k in sorted(needed) if k not in have)
    if missing:
        m = re.search(r"\(lib_symbols\n", src)
        if not m:
            raise Bad("the page has no lib_symbols block")
        src = src[:m.end()] + missing + src[m.end():]
    close = src.rstrip().rfind(")")
    return src[:close] + body + src[close:]


def write_page(board, project, root, page, rows, blocks, fixed):
    name = f"{project}-{slug(page)}.kicad_sch"
    path = Path(board) / name
    path_uuid = f"/{root}/{uid(project, 'sheet', page)}"
    needed = {r["symbol"] for r in rows}

    if path.exists():
        src = path.read_text()
        fresh = [r for r in rows if r["uuid"] not in existing_uuids(src)]
        if not fresh:
            return name, 0, len(rows)
        paper = paper_of(src)
        width, height = PAPERS.get(paper, PAPERS["A"])
        body, _, _ = flow(fresh, blocks, project, path_uuid, width,
                          snap(lowest_used(src) + 10 * GRID), height)
        path.write_text(merge_sheet(src, blocks, needed, body))
        return name, len(fresh), len(rows) - len(fresh)

    lib_symbols = "".join(indent_block(blocks[k], 1).rstrip() + "\n"
                          for k in sorted(needed))

    def lay(width, height=None):
        return flow(rows, blocks, project, path_uuid, width, 5 * GRID,
                    height)

    paper, body = fit_paper(lay, fixed)
    path.write_text(new_sheet(project, uid(project, "file", page), paper,
                              lib_symbols, body))
    return name, len(rows), 0


def write_root(board, project, root, pages, fixed):
    """The root carries one sheet symbol per page and no parts of its own.
    A page already on it keeps its symbol; a new page is added below."""
    path = Path(board) / f"{project}.kicad_sch"
    src = path.read_text() if path.exists() else None
    already = set(re.findall(r'\(property "Sheetname" "([^"]*)"', src or ""))
    fresh = [p for p in pages if p not in already]

    def lay(width, height=None, start=5 * GRID, only=pages):
        body, y = "", start
        for n, page in enumerate(only, start=2):
            body += sheet_sexp(project, root, page,
                               f"{project}-{slug(page)}.kicad_sch",
                               n, 5 * GRID, y, 25 * GRID, 10 * GRID)
            y = snap(y + 14 * GRID)
        return body, y + 5 * GRID, 35 * GRID

    paths = "".join(
        f"\t\t(path \"/{root}/{uid(project, 'sheet', page)}\"\n"
        f"\t\t\t(page \"{n}\")\n\t\t)\n"
        for n, page in enumerate(pages, start=2))

    if src is None:
        paper, sheets = fit_paper(lay, fixed)
        path.write_text(
            "(kicad_sch\n"
            f"\t(version {SCH_VERSION})\n"
            "\t(generator \"kicad-update.py\")\n"
            "\t(generator_version \"10.0\")\n"
            f"\t(uuid \"{root}\")\n"
            f"\t(paper \"{paper}\")\n"
            "\t(lib_symbols\n\t)\n"
            f"{sheets}"
            "\t(sheet_instances\n\t\t(path \"/\"\n\t\t\t(page \"1\")\n\t\t)\n"
            f"{paths}\t)\n"
            "\t(embedded_fonts no)\n"
            ")\n")
        return len(pages)

    if fresh:
        body, _ = lay(PAPERS[paper_of(src)][0],
                      snap(lowest_used(src) + 14 * GRID), fresh)
        close = src.rstrip().rfind(")")
        src = src[:close] + body + src[close:]
    src = re.sub(r"\(sheet_instances\n(?:.*?\n)*?\t\)\n",
                 "(sheet_instances\n\t\t(path \"/\"\n\t\t\t(page \"1\")\n\t\t)\n"
                 + paths + "\t)\n", src, count=1)
    path.write_text(src)
    return len(fresh)


def write_project_file(board, project):
    path = Path(board) / f"{project}.kicad_pro"
    if path.exists():
        return False
    path.write_text(json.dumps({
        "board": {},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{project}.kicad_pro", "version": 3},
        "sheets": [],
        "text_variables": {},
    }, indent=2) + "\n")
    return True


# ------------------------------------------------------------------------ run

def main(argv):
    ap = argparse.ArgumentParser(add_help=True, description=__doc__)
    ap.add_argument("board", help="the KiCad project directory")
    ap.add_argument("--push", action="store_true",
                    help="record to library: rewrite the T2.11 fields")
    ap.add_argument("--pull", action="store_true",
                    help="library to record: read the T2.11 fields back")
    ap.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"),
                    help="rename a part: parts_table.name, the library "
                         "symbol, every sheet lib_id, one pass")
    ap.add_argument("--assign", action="append", default=[],
                    metavar="UUID=IPN",
                    help="the part a placed symbol is, when its ipn field "
                         "does not say")
    args = ap.parse_args(argv[1:])
    assign = {}
    for item in args.assign:
        if "=" not in item:
            raise Bad(f"--assign takes <uuid>=<ipn>, not '{item}'")
        u, ipn = item.split("=", 1)
        if not IPN.match(ipn):
            raise Bad(f"'{ipn}' does not read as an IPN")
        assign[u.strip()] = ipn

    board = Path(args.board)
    if not board.is_dir():
        raise Bad(f"{board} is not a directory")

    # T2.13 - the database is master for the name; nothing is derived.
    con = connect(board)
    try:
        row = con.execute("select name from project_table").fetchone()
    finally:
        con.close()
    if not row:
        raise Bad("project_table is empty. Run init-pipeline first")
    project = row[0]

    if args.rename:
        old, new = args.rename
        library = board / "lib" / f"{project}.kicad_sym"
        con = connect(board)
        try:
            row = con.execute(
                "select ipn, symbol, name from parts_table "
                "where name = ? or symbol = ?",
                (old, f"{project}:{old}")).fetchone()
            if row is None:
                raise Bad(f"no part named '{old}'")
            ipn, symbol, _ = row
            if con.execute("select 1 from parts_table where name = ?",
                           (new,)).fetchone():
                raise Bad(f"'{new}' is already a part's name")
            con.execute("update parts_table set name = ? where ipn = ?",
                        (new, ipn))
            renamed_lib = False
            written = []   # (path, original text) - restored on any failure
            if symbol == f"{project}:{old}":
                src = library.read_text()
                if f'(symbol "{old}"' not in src:
                    raise Bad(f"{library} does not hold '{old}'")
                written.append((library, src))
                src = src.replace(f'(symbol "{old}"', f'(symbol "{new}"')
                src = src.replace(f'(symbol "{old}_', f'(symbol "{new}_')
                src = src.replace(f'(extends "{old}"', f'(extends "{new}"')
                library.write_text(src)
                normalize_lib(library)
                con.execute("update parts_table set symbol = ? "
                            "where ipn = ?", (f"{project}:{new}", ipn))
                for path in sorted(board.glob(f"{project}-*.kicad_sch")):
                    text = path.read_text()
                    if f"{project}:{old}" not in text:
                        continue
                    written.append((path, text))
                    text = text.replace(f'"{project}:{old}"',
                                        f'"{project}:{new}"')
                    text = text.replace(f'(symbol "{project}:{old}_',
                                        f'(symbol "{project}:{new}_')
                    # The sheet's embedded lib copy names its unit blocks
                    # bare - (symbol "OLD_0_1") - nickname stripped (n3.12)
                    text = text.replace(f'(symbol "{old}_',
                                        f'(symbol "{new}_')
                    path.write_text(text)
                    run = subprocess.run(
                        ["kicad-cli", "sch", "upgrade", "--force",
                         str(path)], capture_output=True, text=True)
                    if run.returncode != 0:
                        raise Bad(f"could not normalize {path}")
                renamed_lib = True
            part = board / "parts" / f"{ipn}-{old}.json"
            if part.exists():
                target = board / "parts" / f"{ipn}-{new}.json"
                written.append((part, part.read_text()))
                target.write_text(part.read_text())
                part.unlink()
            con.commit()
            print(f"renamed  {old} -> {new}"
                  + ("  (library and sheets)" if renamed_lib
                     else "  (record only)"))
        except BaseException:
            # One pass or no pass - the record rolls back with the
            # connection; the files roll back here (n3.12).
            for path, text in written:
                path.write_text(text)
                twin = path.parent / path.name.replace(f"-{old}.json",
                                                       f"-{new}.json")
                if twin != path and twin.exists():
                    twin.unlink()
            raise
        finally:
            con.close()
        return 0

    if args.push and args.pull:
        raise Bad("--push or --pull, not both")
    if args.push or args.pull:
        library = board / "lib" / f"{project}.kicad_sym"
        if not library.exists():
            raise Bad(f"{library} does not exist. Run init-pipeline first")
        con = connect(board)
        try:
            if args.push:
                changed = push_fields(con, library, project)
                print(f"push  {len(changed)} symbol(s) updated"
                      + (": " + " ".join(changed) if changed else ""))
                root_path = board / f"{project}.kicad_sch"
                if not root_path.exists():
                    raise Bad(f"{root_path} does not exist. Run "
                              "init-pipeline first")
                sheets = push_instances(con, board, project,
                                        root_path.read_text())
                print(f"push  {sum(sheets.values())} instance(s) updated on "
                      f"{len(sheets)} sheet(s)")
            else:
                applied, reported = pull_fields(con, library, project)
                print(f"pull  {len(applied)} field(s) into the record")
                for ipn, field, held, have in applied:
                    print(f"    {ipn}  {field}  {held!r} -> {have!r}")
                for ipn, field, held, have in reported:
                    if field == "ipn":
                        print(f"    {ipn}  never pushed - no ipn field on "
                              "the library symbol; pull skipped it. Push "
                              "first")
                    else:
                        print(f"    {ipn}  {field}  library {have!r} != "
                              f"record {held!r} - reported only, not "
                              "written")
        finally:
            con.close()
        return 0

    # n8.4: instance paths are rooted at the ROOT SHEET'S OWN uuid -
    # init-pipeline wrote it. Inventing one makes KiCad repair every
    # path on load.
    root_path = board / f"{project}.kicad_sch"
    if not root_path.exists():
        raise Bad(f"{root_path} does not exist. Run init-pipeline first")
    root_src = root_path.read_text()
    found = re.search(r'\(uuid "([0-9a-f-]{36})"\)', root_src)
    if not found:
        raise Bad(f"{root_path} carries no uuid")
    root = found.group(1)

    # The return direction. Read every page back before anything is placed,
    # and enter what the User put there.
    placed = read_pages(board, project, root_src)
    con = connect(board)
    try:
        known = {r["uuid"] for r in instances(con)}
        parts = {r[0] for r in con.execute("select ipn from parts_table")}
        refs = {r[0]: r[1] for r in con.execute(
            "select ref, uuid from ref_table where ref is not null")}
        entered, unresolved, conflicts, fixes = [], [], [], {}
        for u, (fname, page, sym) in sorted(placed.items()):
            if u in known:
                continue
            ipn = assign.get(u) or sym["props"].get("ipn", "").strip()
            if not ipn or ipn not in parts:
                unresolved.append((u, fname, sym))
                continue
            ref = sym["props"].get("Reference", "").strip()
            if ref in refs and refs[ref] != u:
                conflicts.append((u, fname, ref, refs[ref]))
                continue
            if not ref or ref.endswith("?"):
                prefix = re.match(r"^[A-Za-z]+", ref or "U")
                ref = next_ref_free(con, prefix.group(0) if prefix else "U",
                                    set(refs))
            con.execute("insert into ref_table (uuid, ipn, parent, ref, page, "
                        "room) values (?, ?, null, ?, ?, null)",
                        (u, ipn, ref, page))
            refs[ref] = u
            entered.append((u, fname, ref, ipn))
            fix = {}
            if sym["props"].get("ipn", "").strip() != ipn:
                fix["ipn"] = ipn
            if sym["props"].get("Reference", "").strip() != ref:
                fix["Reference"] = ref
            if fix:
                fixes.setdefault(fname, {})[u] = fix
        con.commit()
        rows = instances(con)
    finally:
        con.close()
    by_uuid = {r["uuid"]: r for r in rows}

    # Multi-unit packages, n9_1.22: the record holds one row per unit.
    # Where the symbol has more units than the record has rows, the
    # missing rows are minted here - in the record first, never invented
    # on a sheet. Same ref, unit numbering from 1.
    lib_path = board / "lib" / f"{project}.kicad_sym"
    lib_src = lib_path.read_text() if lib_path.exists() else ""
    lib_spans = lib_blocks(lib_src)
    con = connect(board)
    try:
        minted = 0
        for ipn, symbol in con.execute(
                "select ipn, symbol from parts_table "
                "where symbol is not null").fetchall():
            name = symbol.partition(":")[2]
            if name not in lib_spans:
                continue
            a, b = lib_spans[name]
            units = unit_count(lib_src[a:b], name)
            if units < 2:
                continue
            for ref, page, room in con.execute(
                    "select ref, page, room from ref_table where ipn = ? "
                    "and (unit is null or unit = 1)", (ipn,)).fetchall():
                con.execute("update ref_table set unit = 1 where ipn = ? "
                            "and ref = ? and (unit is null or unit = 1)",
                            (ipn, ref))
                have = {r[0] for r in con.execute(
                    "select unit from ref_table where ipn = ? and ref = ?",
                    (ipn, ref))}
                for u in range(2, units + 1):
                    if u in have:
                        continue
                    con.execute(
                        "insert into ref_table (uuid, ipn, parent, ref, "
                        "page, room, unit) values (?, ?, null, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), ipn, ref, page, room, u))
                    minted += 1
        con.commit()
        if minted:
            rows = instances(con)
            by_uuid = {r["uuid"]: r for r in rows}
            print(f"{minted} unit row(s) minted in ref_table")
    finally:
        con.close()

    # An instance already on some page is never placed again, even when
    # the record now names another page: moving it would cut its wires.
    mismatched = [(by_uuid[u]["ref"], fname, by_uuid[u]["page"])
                  for u, (fname, page, sym) in sorted(placed.items())
                  if u in by_uuid and by_uuid[u]["page"] not in ("", page)]
    elsewhere = {u for u, (fname, page, sym) in placed.items()
                 if u in by_uuid and by_uuid[u]["page"] != page}

    unplaced = [r for r in rows if not r["page"]]
    nosymbol = [r for r in rows if r["page"] and not r["symbol"]]
    drawable = [r for r in rows if r["page"] and r["symbol"]
                and r["uuid"] not in elsewhere]
    if not drawable and not placed:
        raise Bad("nothing to place. Every instance is missing a page, a "
                  "symbol, or both")

    blocks = library_blocks(board, project, {r["symbol"] for r in drawable})
    pages = sorted({r["page"] for r in drawable})

    # An entered symbol gets its `ipn` field, and its Reference when the
    # tool renumbered it. Nothing else on a sheet is rewritten - the User
    # pulls fields in KiCad, Update Symbols from Library (n9_1.9).
    refreshed = {}
    for fname, by_id in fixes.items():
        path = board / fname
        src = path.read_text()
        out, last, count = [], 0, 0
        for start, end in symbol_blocks(src):
            block = src[start:end]
            fix = by_id.get(read_symbol(block)["uuid"])
            if not fix:
                continue
            new = block
            for name, value in fix.items():
                new = (set_reference(new, value) if name == "Reference"
                       else set_property(new, name, value))
            if new != block:
                out.append(src[last:start]); out.append(new); last = end
                count += 1
        if count:
            out.append(src[last:])
            path.write_text("".join(out))
            refreshed[path.name] = count

    def normalize(path):
        """n8.2 - the sheet must load with no dialogs. KiCad's own writer
        has the final word on the format."""
        run = subprocess.run(["kicad-cli", "sch", "upgrade", "--force",
                              str(path)], capture_output=True, text=True)
        if run.returncode != 0:
            raise Bad(f"kicad-cli could not normalize {path}: "
                      + (run.stderr or run.stdout).strip())

    made = write_project_file(board, project)
    added = write_root(board, project, root, pages, None)
    normalize(board / f"{project}.kicad_sch")
    print(f"{project}.kicad_pro  {'written' if made else 'kept'}")
    print(f"{project}.kicad_sch  {added} page(s) added, {len(pages)} in all")

    touched = set(refreshed)
    for page in pages:
        on_page = [r for r in drawable if r["page"] == page]
        name, new, kept = write_page(board, project, root, page, on_page,
                                     blocks, None)
        touched.discard(name)
        normalize(board / name)
        came_in = sum(1 for _, f, _, _ in entered if f == name)
        print(f"    {name}  {new} placed, {kept} left as they were, "
              f"{came_in} entered")
    for name in sorted(touched):
        normalize(board / name)

    if entered:
        print(f"\n{len(entered)} symbol(s) entered in ref_table from the "
              "sheets: " + " ".join(f"{ref}={ipn}" for _, _, ref, ipn in entered))
    if unresolved:
        print(f"\n{len(unresolved)} symbol(s) on a sheet with no part in "
              "the record. Name each with --assign <uuid>=<ipn>:")
        for u, fname, sym in unresolved:
            print(f"    {u}  {fname}  {sym['lib_id']}  "
                  f"Reference={sym['props'].get('Reference', '')!r}  "
                  f"Value={sym['props'].get('Value', '')!r}  "
                  f"ipn={sym['props'].get('ipn', '')!r}")
    if conflicts:
        print(f"\n{len(conflicts)} symbol(s) whose Reference is held by "
              "another instance, left as they are:")
        for u, fname, ref, other in conflicts:
            print(f"    {u}  {fname}  {ref} is {other}")
    if mismatched:
        print(f"\n{len(mismatched)} instance(s) on a page other than the "
              "record's, left where they are: "
              + " ".join(f"{ref}:{f}!={p}" for ref, f, p in mismatched))
    if nosymbol:
        print(f"\n{len(nosymbol)} instance(s) with a page and no symbol: "
              + " ".join(sorted(r["ref"] for r in nosymbol)))
    if unplaced:
        print(f"{len(unplaced)} instance(s) with no page, not drawn: "
              + " ".join(sorted(r["ref"] for r in unplaced)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
