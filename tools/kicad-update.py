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
        name.replace("-", "_"), HERE / f"{name}.py")
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
    if not {"parts_table", "ref_table", "aml_table", "mpn_table"} <= have:
        raise Bad(f"{path} is missing a table. Run init-pipeline")
    return con


def instances(con):
    """One row per thing to draw, with everything the sheet needs on it.

    `Value` shows the part number the board was designed against — the
    blank-rank row of T2.6 — and falls back to the description for a part
    that has no part number yet. The IPN is on the row either way, in its
    own field, and that is what the record keys on."""
    rows = []
    for uuid_, ipn, ref, page, room, symbol, footprint, description in con.execute(
            "select r.uuid, r.ipn, r.ref, r.page, r.room, "
            "       p.symbol, p.footprint, p.description "
            "from ref_table r join parts_table p on p.ipn = r.ipn "
            "order by r.ipn, r.ref"):
        mpn = con.execute(
            "select mpn from aml_table where ipn = ? "
            "order by rank is not null, rank", (ipn,)).fetchone()
        rows.append({
            "uuid": uuid_, "ipn": ipn, "ref": ref or "",
            "page": (page or "").strip(), "room": (room or "").strip(),
            "symbol": symbol, "footprint": footprint or "",
            "value": (mpn[0] if mpn else None) or description or ipn,
        })
    return rows


def order_of(row):
    """T2.12, ranks 2 and 3. Rooms come first, in name order. What is left
    groups by family — the class letter of the IPN — and runs by reference
    inside it."""
    ref = re.match(r"^([A-Za-z]+)(\d+)$", row["ref"])
    number = int(ref.group(2)) if ref else 0
    if row["room"]:
        return (0, row["room"], "", number)
    return (1, "", IPN.match(row["ipn"]).group(1), number)


# ------------------------------------------------------------------ the symbol

def property_sexp(name, value, x, y, hide=False):
    hidden = "\t\t\t\t(hide yes)\n" if hide else ""
    return (
        f"\t\t(property \"{name}\" \"{value}\"\n"
        f"\t\t\t(at {x} {y} 0)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size {FONT} {FONT})\n"
        f"\t\t\t\t)\n{hidden}\t\t\t)\n"
        f"\t\t)\n"
    )


def instance_sexp(project, path_uuid, row, x, y, clear):
    return (
        "\t(symbol\n"
        f"\t\t(lib_id \"{row['symbol']}\")\n"
        f"\t\t(at {x:.2f} {y:.2f} 0)\n"
        "\t\t(unit 1)\n"
        "\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
        "\t\t(dnp no)\n\t\t(fields_autoplaced yes)\n"
        f"\t\t(uuid \"{row['uuid']}\")\n"
        + property_sexp("Reference", row["ref"], f"{x:.2f}", f"{y - clear:.2f}")
        + property_sexp("Value", row["value"], f"{x:.2f}", f"{y + clear:.2f}")
        + property_sexp("Footprint", row["footprint"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("ipn", row["ipn"], f"{x:.2f}", f"{y:.2f}", hide=True)
        + "\t\t(instances\n"
        f"\t\t\t(project \"{project}\"\n"
        f"\t\t\t\t(path \"{path_uuid}\"\n"
        f"\t\t\t\t\t(reference \"{row['ref']}\")\n\t\t\t\t\t(unit 1)\n"
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


def extent(block):
    """Half-width and half-height of everything drawn, so the layout can give
    each part the room it takes."""
    xs, ys = [], []
    for x, y in re.findall(r'\((?:start|end|xy|at) (-?[\d.]+) (-?[\d.]+)', block):
        xs.append(abs(float(x)))
        ys.append(abs(float(y)))
    return (max(xs) if xs else GRID), (max(ys) if ys else GRID)


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
    anchor = block.find("\n\t\t(pin ")
    if anchor < 0:
        anchor = block.find("\n\t\t(instances")
    if anchor < 0:
        raise Bad("a symbol block with no pins and no instances")
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


# ---------------------------------------------------------------- the placing

def flow(rows, blocks, project, path_uuid, width, start_y):
    """Lay parts left to right, wrapping at the page edge. A new room or a
    new family starts on a new row, so the groups of T2.12 read as groups."""
    margin = 5 * GRID
    gap = 5 * GRID
    cur_x, cur_y, row_h = margin, start_y, 0.0
    page_h, body, group = start_y, "", None

    for row in sorted(rows, key=order_of):
        key = order_of(row)[:3]
        if group is not None and key != group:
            cur_x, cur_y, row_h = margin, snap(cur_y + row_h + gap), 0.0
        group = key

        half_w, half_h = extent(blocks[row["symbol"]])
        clear = half_h + GRID
        if cur_x + 2 * half_w > width - margin and cur_x > margin:
            cur_x, cur_y, row_h = margin, snap(cur_y + row_h + gap), 0.0
        x = snap(cur_x + half_w)
        y = snap(cur_y + clear + half_h)
        cur_x = snap(x + half_w + gap)
        row_h = max(row_h, 2 * (clear + half_h))
        page_h = max(page_h, y + clear + half_h + margin)
        body += instance_sexp(project, path_uuid, row, x, y, clear)
    return body, page_h


def fit_paper(lay, fixed):
    """Lay the page out on each sheet size in turn and take the first it
    fits."""
    sizes = [fixed] if fixed else PAPER_ORDER
    for name in sizes:
        width, height = PAPERS[name]
        drawn, used = lay(width)
        if used <= height or name == sizes[-1]:
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
        width = PAPERS.get(paper, PAPERS["A"])[0]
        body, _ = flow(fresh, blocks, project, path_uuid, width,
                       snap(lowest_used(src) + 10 * GRID))
        path.write_text(merge_sheet(src, blocks, needed, body))
        return name, len(fresh), len(rows) - len(fresh)

    lib_symbols = "".join(indent_block(blocks[k], 1).rstrip() + "\n"
                          for k in sorted(needed))

    def lay(width):
        return flow(rows, blocks, project, path_uuid, width, 5 * GRID)

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

    def lay(width, start=5 * GRID, only=pages):
        body, y = "", start
        for n, page in enumerate(only, start=2):
            body += sheet_sexp(project, root, page,
                               f"{project}-{slug(page)}.kicad_sch",
                               n, 5 * GRID, y, 25 * GRID, 10 * GRID)
            y = snap(y + 14 * GRID)
        return body, y + 5 * GRID

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
        entered, unresolved, conflicts = [], [], []
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
        con.commit()
        rows = instances(con)
    finally:
        con.close()
    by_uuid = {r["uuid"]: r for r in rows}

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

    # Fields: the record is master, 2.2. Rewrite what differs, in place.
    refreshed = {}
    for path in sorted(board.glob(f"{project}-*.kicad_sch")):
        src = path.read_text()
        out, last, count = [], 0, 0
        for start, end in symbol_blocks(src):
            block = src[start:end]
            sym = read_symbol(block)
            row = by_uuid.get(sym["uuid"])
            if row is None:
                continue
            want = {"Reference": row["ref"], "Value": row["value"],
                    "Footprint": row["footprint"], "ipn": row["ipn"]}
            new = block
            for name, value in want.items():
                if sym["props"].get(name) != value:
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
              f"{refreshed.get(name, 0)} field(s) refreshed, "
              f"{came_in} entered")
    for name in sorted(touched):
        normalize(board / name)
        print(f"    {name}  {refreshed[name]} field(s) refreshed")

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
