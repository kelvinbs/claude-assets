#!/usr/bin/env python3
"""kicad-update — carry the record onto the pages, and the pages back.

    kicad-update.py <board-dir> [--assign <uuid>=<ipn> ...]

The skill of stages 4 and 6. Every instance in `ref_table` that carries a
page and whose part carries a symbol is drawn on that page, in the order of
T2.12: room first, then family for the instances with no room. Every net
the record names is a local or a hierarchical label, never a global one;
the root joins the pages by sheet pins, and buses carry grouped nets.

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
SHEET_W = 20 * GRID    # a sheet symbol's width; its height follows its pins
PORT_W = 16 * GRID     # the port area's width, kept clear at a page's right edge
TOP = 12 * GRID        # first row of drawings; a top pin's label stands above its symbol
STUB = 2 * GRID        # wire or bus from a sheet pin to its label
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

ROOM_ROWS = []
PARENT_OF, SYM_OF, KIND_OF = {}, {}, {}


def sheet_path_of(nid, sheet_ids):
    """The KiCad sheet path of a node: the sym_uuids of its
    sheet-instance ancestors, root first, '/'-joined. '' on a root
    page. T2.4 - walked, never stored."""
    chain, walk, seen = [], PARENT_OF.get(nid), {nid}
    while walk:
        # A28: bounded. A ring has no root, so an unbounded walk spins with
        # no error at all; this raises at once and names where it closed
        if walk in seen:
            raise Bad(f"parent chain closes a loop at {walk}")
        seen.add(walk)
        if walk in sheet_ids:
            chain.append(SYM_OF.get(walk) or walk)
        walk = PARENT_OF.get(walk)
    return "/".join(reversed(chain))


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
    """T2.11: parts_table.value, else the IPN."""
    value = con.execute("select value from parts_table where ipn = ?",
                        (ipn,)).fetchone()
    return (value and value[0]) or ipn


def instances(con):
    """One row per instance, (uuid, path), with everything the sheet needs
    on it. A symbol in a sub-sheet is one drawing and several rows.

    `Value` is parts_table.value. The IPN is on the row in its own field,
    and that is what the record keys on."""
    global ROOM_ROWS
    ROOM_ROWS = [dict(zip(("id", "sym_uuid", "name", "parent", "page",
                           "corner"), r))
                 for r in con.execute(
                     "select id, sym_uuid, name, parent, page, corner "
                     "from ref_table where kind = 'room'")]
    # T2.4: the record holds the tree. A node's sheet path is the chain of
    # its sheet-instance parents from the root, so it is walked, not stored
    global PARENT_OF, SYM_OF, KIND_OF
    PARENT_OF, SYM_OF, KIND_OF = {}, {}, {}
    for i, sy, pa, ki in con.execute(
            "select id, sym_uuid, parent, kind from ref_table"):
        PARENT_OF[i], SYM_OF[i], KIND_OF[i] = pa, sy, ki
    sheet_ids = {i for i, sym in con.execute(
        "select r.id, p.symbol from ref_table r join parts_table p "
        "on p.ipn = r.ipn where r.kind = 'part'")
        if (sym or "").startswith("sheet:")}
    rows = []
    for nid, sym_uuid, ipn, ref, page, unit, symbol, footprint, \
            description, datasheet, manufacturer, mpn, note, checked, \
            name, x, y, rot, placed in con.execute(
            "select r.id, r.sym_uuid, r.ipn, r.ref, r.page, r.unit, "
            "       p.symbol, p.footprint, p.description, p.datasheet, "
            "       p.manufacturer, p.mpn, p.note, p.checked, p.name, "
            "       r.x, r.y, r.rot, r.placed "
            "from ref_table r join parts_table p on p.ipn = r.ipn "
            "where r.kind = 'part' "
            "order by r.ipn, r.ref, r.unit"):
        rows.append({
            "x": x, "y": y, "rot": rot or 0, "placed": placed or "",
            "id": nid, "uuid": sym_uuid or nid, "ipn": ipn, "ref": ref or "",
            "page": (page or "").strip(),
            "unit": unit or 1,
            "path": sheet_path_of(nid, sheet_ids),
            "symbol": symbol, "footprint": footprint or "",
            "value": value_of(con, ipn, description),
            "description": description or "", "datasheet": datasheet or "",
            "manufacturer": manufacturer or "", "mpn": mpn or "",
            "note": note or "", "checked": checked or "no",
            "parent": "",
            "sheet": name if (symbol or "").startswith("sheet:") else "",
        })
    # T2.11: `parent` reaches the sheet as the parent instance's reference,
    # not its uuid. A reference is what an engineer reads on a page. A
    # drawing shared by several instances names every parent, in order
    ref_of = {r["id"]: r["ref"] for r in rows}
    for r in rows:
        pid = PARENT_OF.get(r["id"])
        r["parent"] = ref_of.get(pid, "")
        r["parent_field"] = r["parent"]
        # one id names one node - one unit, or one room. T2.4
        r["parent_key"] = pid or ""
    by_uuid = {}
    for r in rows:
        by_uuid.setdefault(r["uuid"], []).append(r)
    for rs in by_uuid.values():
        if len(rs) > 1:
            parents = [x["parent"] for x in rs if x["parent"]]
            joined = " ".join(dict.fromkeys(parents))
            for x in rs:
                x["parent_field"] = joined
    return rows


def drawings(rows):
    """One row per symbol to draw - the first instance of each uuid - with
    `paths`: [(path, ref, unit)] for every instance it stands for."""
    out, seen = [], {}
    for r in rows:
        if r["uuid"] not in seen:
            seen[r["uuid"]] = dict(r, paths=[])
            out.append(seen[r["uuid"]])
        seen[r["uuid"]]["paths"].append((r["path"], r["ref"], r["unit"]))
    return out


def root_page_of(rows):
    """The root page each sheet-instance chain starts on: uuid of a
    sub-sheet instance placed on a root page -> that page."""
    return {r["uuid"]: r["page"] for r in rows
            if r["sheet"] and not r["path"]}


def kicad_path(root, sheets, starts, page, path):
    """The KiCad instance path of a sheet instance: the root, the root
    page's sheet symbol, then the chain of sub-sheet symbols."""
    if not path:
        if page not in sheets:
            raise Bad(f"root sheet has no sheet symbol for page {page!r}")
        return f"/{root}/{sheets[page][0]}"
    first = path.split("/")[0]
    if first not in starts:
        raise Bad(f"instance path {path} starts at no sub-sheet on a root "
                  "page")
    return f"/{root}/{sheets[starts[first]][0]}/{path}"


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
    if row.get("room"):
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


def instance_sexp(project, path_uuid, row, x, y, bottom, right, rot=0):
    unit = row.get("unit") or 1
    first = unit == 1
    return (
        "\t(symbol\n"
        f"\t\t(lib_id \"{row['symbol']}\")\n"
        f"\t\t(at {x:.2f} {y:.2f} {int(rot) % 360})\n"
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
        + property_sexp("parent", row["parent_field"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("checked", row["checked"], f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + instances_sexp(project, row, path_uuid)
        + "\t)\n"
    )


def instances_sexp(project, row, path_of):
    """The per-instance block: one path entry per instance the drawing
    stands for, each with its own reference. `path_of` maps a record
    path to the KiCad path, or is the one KiCad path when the row has
    no `paths`."""
    entries = row.get("paths") or [(row.get("path", ""), row["ref"],
                                    row.get("unit") or 1)]
    body = ("\t\t(instances\n"
            f"\t\t\t(project \"{project}\"\n")
    for path, ref, unit in entries:
        kp = path_of(path) if callable(path_of) else path_of
        body += (f"\t\t\t\t(path \"{kp}\"\n"
                 f"\t\t\t\t\t(reference \"{esc(ref)}\")\n"
                 f"\t\t\t\t\t(unit {unit})\n"
                 "\t\t\t\t)\n")
    return body + "\t\t\t)\n\t\t)\n"


def nets_of(con):
    """net_table as {node id: {pin: net}}. A net belongs to the node, and
    a drawing in a sub-sheet is one node per instance - T2.4."""
    out = {}
    for i, pin, net in con.execute("select id, pin, net from net_table"):
        out.setdefault(i, {})[pin] = net
    return out


def is_bus(name):
    return name.startswith("{") and name.endswith("}")


class Nets:
    """The net model of TB.4: what label a net takes on a page, and what
    pins a page's sheet symbol carries. Derived from the record alone.

    A net that appears in one sheet file is local. One that appears in
    two or more leaves each by a hierarchical label and a sheet pin. A
    net in a bus is local on its page and travels as the bus. A
    sub-sheet's pins are also whatever its instances name on their pins."""

    def __init__(self, con, rows):
        self.pins = nets_of(con)
        self.bus_of = dict(con.execute("select net, bus from bus_table"))
        self.members = {}
        for net, bus in self.bus_of.items():
            self.members.setdefault(bus, []).append(net)
        page_of = {r["id"]: r["page"] for r in rows}
        self.sheet_pages = {r["sheet"] for r in rows if r["sheet"]}
        # what sits under a page: the sub-sheets instanced on it, and
        # theirs. A net shared only inside that subtree does not leave
        # the page
        below = {}
        for r in rows:
            if r["sheet"]:
                below.setdefault(r["page"], set()).add(r["sheet"])
        self.under = {}
        for page in set(below) | self.sheet_pages | set(page_of.values()):
            seen, todo = set(), list(below.get(page, ()))
            while todo:
                x = todo.pop()
                if x not in seen:
                    seen.add(x)
                    todo.extend(below.get(x, ()))
            self.under[page] = seen
        # a multi-unit package is one instance under one reference; its
        # nets may be recorded against any of its unit rows, and each
        # unit draws the pins it shows
        self.by_ref = {}
        for r in rows:
            self.by_ref.setdefault((r["page"], r["ref"]), {}).update(
                self.pins.get(r["id"], {}))
        self.files, self.bus_files, self.declared = {}, {}, {}
        for r in rows:
            if not r["sheet"]:
                continue
            for pin in self.pins.get(r["id"], {}):
                self.declared.setdefault(r["sheet"], set()).add(pin)
        for u, pins in self.pins.items():
            page = page_of.get(u)
            if not page:
                continue
            for pin, net in pins.items():
                if is_bus(net):
                    self.bus_files.setdefault(net[1:-1], set()).add(page)
                    continue
                self.files.setdefault(net, set()).add(page)
                if net in self.bus_of:
                    self.bus_files.setdefault(self.bus_of[net],
                                              set()).add(page)

    def pins_for(self, row):
        """{pin: net} for a drawing: every net named on any unit row of
        its reference on its page."""
        return self.by_ref.get((row["page"], row["ref"])) \
            or self.pins.get(row["id"]) or {}

    def outside(self, files, page):
        """Whether something on `page` is also somewhere outside the page
        and the sub-sheets under it."""
        return bool(files - {page} - self.under.get(page, set()))

    def kind(self, net, page):
        """The label at a pin is always local. A net that leaves the page
        gets its hierarchical label in the port area, joined by name."""
        return "label"

    def hier(self, net, page):
        """Whether this net leaves this page: outside the page and what is
        under it, or named as a pin of this sub-sheet."""
        if is_bus(net) or net in self.bus_of:
            return False
        if self.outside(self.files.get(net, set()), page):
            return True
        return net in self.declared.get(page, ())

    def buses(self, page):
        """The buses that leave this page, by name, sorted."""
        out = {b for b, fs in self.bus_files.items()
               if page in fs and self.outside(fs, page)}
        out |= {p[1:-1] for p in self.declared.get(page, ()) if is_bus(p)}
        return sorted(out)

    def leaving(self, page):
        """The nets that leave this page by their own pin, sorted."""
        out = {n for n, fs in self.files.items() if page in fs
               and self.hier(n, page)}
        out |= {p for p in self.declared.get(page, ()) if not is_bus(p)}
        return sorted(out)

    def sheet_pins(self, page):
        """The pins of a sheet symbol standing for this page: buses first,
        then nets. Each as the name written on the pin."""
        return ([f"{{{b}}}" for b in self.buses(page)]
                + self.leaving(page))

    def aliases(self):
        return {b: sorted(ns) for b, ns in self.members.items()}


def pin_points(block, unit):
    """(number, x, y, rotation, length) of every pin the instance shows: the
    unit's own sub-symbols and the shared unit 0. Library coordinates, y up."""
    out = []
    for m in re.finditer(r'\(symbol "[^"]*_(\d+)_\d+"\n((?:.*\n)*?)\t\t\)',
                         block):
        u = int(m.group(1))
        if u not in (0, unit):
            continue
        for p in re.finditer(r'\(pin \w+ \w+\n\s*\(at (-?[\d.]+) (-?[\d.]+)'
                             r' (-?[\d.]+)\)\n\s*\(length ([\d.]+)\)'
                             r'(?:.*\n)*?\s*\(number "([^"]*)"',
                             m.group(2)):
            out.append((p.group(5), float(p.group(1)), float(p.group(2)),
                        int(float(p.group(3))) % 360, float(p.group(4))))
    return out


def pin_ends(block, unit, x, y, rot):
    """Sheet coordinates of every pin end of an instance placed at (x, y)
    with rotation rot: {number: (X, Y, pin rotation on the sheet)}."""
    out = {}
    a = math.radians(rot)
    for num, px, py, prot, plen in pin_points(block, unit):
        # `at` is the connection point, the free end. Library y is up,
        # sheet y is down
        dx, dy = px, -py
        rx = dx * math.cos(a) - dy * math.sin(a)
        ry = dx * math.sin(a) + dy * math.cos(a)
        # label angle is the way the text extends: away from the body,
        # opposite the pin's own rotation
        out[num] = (round(x + rx, 2), round(y + ry, 2), (prot - rot + 180) % 360)
    return out


def label_sexp(net, x, y, angle, kind="label", uuid_=None):
    """A label at a point. `label` is local to the sheet file;
    `hierarchical_label` pairs with a pin on the sheet symbol above.
    Never a global label."""
    shape = "\t\t(shape passive)\n" if kind == "hierarchical_label" else ""
    return (
        f"\t({kind} \"{esc(net)}\"\n"
        f"{shape}"
        f"\t\t(at {x:.2f} {y:.2f} {angle})\n"
        "\t\t(fields_autoplaced yes)\n"
        f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {FONT} {FONT})\n\t\t\t)\n"
        # KiCad pairs the angle with a justify; without it kicad-cli
        # upgrade folds 180 to 0 and 270 to 90
        f"\t\t\t(justify {'right' if angle in (180, 270) else 'left'})\n"
        "\t\t)\n"
        f"\t\t(uuid \"{uuid_ or uid('label', net, f'{x:.2f}', f'{y:.2f}')}\")\n"
        "\t)\n"
    )


def stub_sexp(x0, y0, x1, y1, bus, uuid_):
    """A short wire, or bus, from a sheet pin to the label that names it."""
    return (
        f"\t({'bus' if bus else 'wire'}\n"
        f"\t\t(pts\n\t\t\t(xy {x0:.2f} {y0:.2f}) (xy {x1:.2f} {y1:.2f})\n\t\t)\n"
        "\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n"
        f"\t\t(uuid \"{uuid_}\")\n"
        "\t)\n"
    )


def sheet_pin_sexp(name, x, y, uuid_):
    return (
        f"\t\t(pin \"{esc(name)}\" passive\n"
        f"\t\t\t(at {x:.2f} {y:.2f} 0)\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size {FONT} {FONT})\n"
        "\t\t\t\t)\n\t\t\t\t(justify right)\n\t\t\t)\n"
        f"\t\t\t(uuid \"{uuid_}\")\n"
        "\t\t)\n"
    )


def sheet_height(npins):
    return max(4 * GRID, (npins + 1) * GRID)


def sheet_pin_points(x, y, w, pins):
    """{pin name: (X, Y)} on the right edge of a sheet symbol at (x, y)."""
    return {name: (round(x + w, 2), round(y + GRID * (i + 1), 2))
            for i, name in enumerate(pins)}


def pin_fittings_sexp(sheet_uuid, x, y, w, pins, labels, kinds):
    """For every pin of a sheet symbol: a stub out to the right and a label
    at its end. `labels` maps pin name to the label text, `kinds` to the
    label kind. Deterministic uuids, so a rerun replaces its own work."""
    body = ""
    for name, (px, py) in sheet_pin_points(x, y, w, pins).items():
        text = labels.get(name)
        if not text:
            continue
        body += stub_sexp(px, py, px + STUB, py, is_bus(text),
                          uid("stub", sheet_uuid, name))
        body += label_sexp(text, px + STUB, py, 0, kinds.get(name, "label"),
                           uid("pinlabel", sheet_uuid, name))
    return body


def labels_sexp(row, block, x, y, rot, model):
    """Labels for every pin of this drawing that net_table names."""
    pins = model.pins_for(row)
    if not pins:
        return ""
    ends = pin_ends(block, row.get("unit") or 1, x, y, rot)
    return "".join(label_sexp(pins[n], *ends[n], model.kind(pins[n], row["page"]))
                   for n in sorted(pins) if n in ends)


def sheet_sexp(project, parent_path, name, filename, page_number, x, y, w,
               pins, sheet_uuid):
    """A sheet symbol with its pins down the right edge."""
    h = sheet_height(len(pins))
    points = sheet_pin_points(x, y, w, pins)
    return (
        "\t(sheet\n"
        f"\t\t(at {x:.2f} {y:.2f})\n"
        f"\t\t(size {w:.2f} {h:.2f})\n"
        "\t\t(fields_autoplaced yes)\n"
        "\t\t(stroke\n\t\t\t(width 0.1524)\n\t\t\t(type solid)\n\t\t)\n"
        "\t\t(fill\n\t\t\t(color 0 0 0 0.0000)\n\t\t)\n"
        f"\t\t(uuid \"{sheet_uuid}\")\n"
        + property_sexp("Sheetname", esc(name), f"{x:.2f}", f"{y - GRID:.2f}")
        + property_sexp("Sheetfile", filename, f"{x:.2f}",
                        f"{y + h + GRID:.2f}")
        + "".join(sheet_pin_sexp(p, *points[p], uid("pin", sheet_uuid, p))
                  for p in pins)
        + "\t\t(instances\n"
        f"\t\t\t(project \"{project}\"\n"
        f"\t\t\t\t(path \"{parent_path}\"\n\t\t\t\t\t(page \"{page_number}\")\n"
        "\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        "\t)\n"
    )


def bus_entry_sexp(x, y, uuid_):
    return (
        "\t(bus_entry\n"
        f"\t\t(at {x:.2f} {y:.2f})\n"
        f"\t\t(size {GRID:.2f} {GRID:.2f})\n"
        "\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n"
        f"\t\t(uuid \"{uuid_}\")\n"
        "\t)\n"
    )


def port_sexp(page, net, x, y):
    """A port: the hierarchical label a leaving net needs, a stub, and a
    local label of the same name, so the label at the pin joins it."""
    return (label_sexp(net, x, y, 180, "hierarchical_label",
                       uid("port", page, net))
            + stub_sexp(x, y, x + STUB, y, False, uid("portstub", page, net))
            + label_sexp(net, x + STUB, y, 0, "label",
                         uid("portlabel", page, net)))


def bus_breakout_sexp(page, bus, members, x, y):
    """A bus that leaves a page, drawn once per page: the hierarchical bus
    label at the top, the bus running down, and one entry per member
    ending in a local label. The member labels at the symbol pins join by
    name, so the page is connected without a wire drawn. The User moves
    it, or draws on from it."""
    n = len(members)
    bottom = y + (n + 1) * GRID
    body = label_sexp(f"{{{bus}}}", x, y, 180, "hierarchical_label",
                      uid("bus", page, bus))
    body += stub_sexp(x, y, x, bottom, True, uid("busstub", page, bus))
    for i, m in enumerate(sorted(members)):
        ey = y + (i + 1) * GRID
        body += bus_entry_sexp(x, ey, uid("busentry", page, bus, m))
        body += stub_sexp(x + GRID, ey + GRID, x + 3 * GRID, ey + GRID, False,
                          uid("buswire", page, bus, m))
        body += label_sexp(m, x + 3 * GRID, ey + GRID, 0, "label",
                           uid("buslabel", page, bus, m))
    return body


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
        if nick == "sheet":
            continue
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


def top_blocks(src, head):
    """Every top-level block opening with `head` - e.g. "(symbol", "(sheet",
    "(wire" - as (start, end) spans of the text. Library definitions sit
    one level down inside `lib_symbols` and are not matched."""
    spans, i = [], 0
    key = f"\n\t{head}"
    while True:
        j = src.find(key, i)
        if j < 0:
            return spans
        after = src[j + len(key):j + len(key) + 1]
        if after not in ("\n", " ", ""):
            i = j + 1
            continue
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


def symbol_blocks(src):
    """Every placed symbol on a page."""
    return top_blocks(src, "(symbol")


def sheet_blocks(src):
    """Every sheet symbol on a page."""
    return top_blocks(src, "(sheet")


FITTINGS = ("(wire", "(bus", "(bus_entry", "(label", "(hierarchical_label",
            "(global_label")


def remove_by_uuid(src, uuids):
    """Drop every wire, bus and label whose uuid is in the set."""
    if not uuids:
        return src
    spans = []
    for head in FITTINGS:
        for a, b in top_blocks(src, head):
            u = re.search(r'\(uuid "([^"]+)"\)', src[a:b])
            if u and u.group(1) in uuids:
                spans.append((a, b))
    for a, b in sorted(spans, reverse=True):
        src = src[:a] + src[b:]
    return src


def signature(head, block):
    """What a fitting is, geometrically: its kind and every point in it."""
    pts = re.findall(r'\((?:at|xy) (-?[\d.]+) (-?[\d.]+)', block)
    return (head, tuple(sorted((round(float(x), 2), round(float(y), 2))
                               for x, y in pts)))


def remove_matching(src, body):
    """Drop every fitting in src that coincides exactly - kind and all its
    points - with one the tool is about to write. A fitting orphaned by an
    earlier run under another uuid goes this way."""
    want = set()
    for head in FITTINGS:
        for a, b in top_blocks(body, head):
            want.add(signature(head, body[a:b]))
    if not want:
        return src
    spans = []
    for head in FITTINGS:
        for a, b in top_blocks(src, head):
            if signature(head, src[a:b]) in want:
                spans.append((a, b))
    for a, b in sorted(spans, reverse=True):
        src = src[:a] + src[b:]
    return src


def read_sheet(block):
    """What a page says about one sheet symbol: uuid, name, file, place,
    size and pin names."""
    u = re.search(r'\n\t\t\(uuid "([^"]+)"\)', block)
    name = re.search(r'\(property "Sheetname" "((?:[^"\\]|\\.)*)"', block)
    file = re.search(r'\(property "Sheetfile" "((?:[^"\\]|\\.)*)"', block)
    at = re.search(r'\n\t\t\(at (-?[\d.]+) (-?[\d.]+)\)', block)
    size = re.search(r'\n\t\t\(size (-?[\d.]+) (-?[\d.]+)\)', block)
    pins = re.findall(r'\n\t\t\(pin "((?:[^"\\]|\\.)*)"', block)
    return {"uuid": u.group(1) if u else None,
            "name": unesc(name.group(1)) if name else "",
            "file": file.group(1) if file else "",
            "at": (float(at.group(1)), float(at.group(2))) if at else (0, 0),
            "size": (float(size.group(1)), float(size.group(2))) if size
            else (SHEET_W, sheet_height(0)),
            "pins": [unesc(p) for p in pins]}


def set_sheet_pins(block, pins, sheet_uuid):
    """Rewrite a sheet symbol's pin list and height for these pins. Its
    place on the page stays."""
    sh = read_sheet(block)
    x, y = sh["at"]
    w = sh["size"][0]
    block = re.sub(r'\n\t\t\(pin "(?:[^"\\]|\\.)*"[^\n]*\n(?:\t\t\t.*\n)*?\t\t\)',
                   "", block)
    block = re.sub(r'\n\t\t\(size [^)]*\)',
                   f"\n\t\t(size {w:.2f} {sheet_height(len(pins)):.2f})",
                   block, count=1)
    points = sheet_pin_points(x, y, w, pins)
    new = "".join(sheet_pin_sexp(p, *points[p], uid("pin", sheet_uuid, p))
                  for p in pins)
    i = block.find("\n\t\t(instances")
    if i < 0:
        i = block.rstrip().rfind("\n")
    return block[:i + 1] + new + block[i + 1:]


def set_instances(block, project, row, path_of):
    """Rewrite a symbol's per-instance block from the record."""
    new = instances_sexp(project, row, path_of)
    i = block.find("\n\t\t(instances\n")
    if i < 0:
        j = block.rstrip().rfind("\n")
        return block[:j + 1] + new + block[j + 1:]
    depth, k, in_str = 0, i + 1, False
    while k < len(block):
        c = block[k]
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
    while k < len(block) and block[k] == "\n":
        k += 1
    return block[:i + 1] + new + block[k:]


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


def root_sheets(root_src):
    """Sheetname -> (sheet uuid, page number), from the root's sheet symbols.
    The uuid on the root is the one the pages must address, whatever
    uid() would compute today - a project renamed keeps its sheets."""
    out = {}
    for m in re.finditer(r'\(sheet\n(.*?)\n\t\)\n', root_src or "", re.S):
        block = m.group(1)
        name = re.search(r'\(property "Sheetname" "([^"]*)"', block)
        uuid_ = re.search(r'\(uuid "([0-9a-f-]{36})"\)', block)
        page = re.search(r'\(page "(\d+)"\)', block)
        if name and uuid_:
            out[name.group(1)] = (uuid_.group(1),
                                  int(page.group(1)) if page else 0)
    return out


def page_names(root_src):
    """Sheetfile to Sheetname, from the root's sheet symbols."""
    names = re.findall(r'\(property "Sheetname" "([^"]*)"', root_src)
    files = re.findall(r'\(property "Sheetfile" "([^"]*)"', root_src)
    return dict(zip(files, names))


def page_files(con, project, root_src):
    """Sheetfile to page name: the root's sheet symbols, plus every
    sub-sheet the record names, whose file is named from its name."""
    by_file = page_names(root_src)
    for (name,) in con.execute("select name from parts_table where symbol "
                               "like 'sheet:%' and name is not null"):
        by_file[f"{project}-{slug(name)}.kicad_sch"] = name
    return by_file


def read_pages(board, project, by_file):
    """Every symbol and every sheet symbol on every page: two maps, uuid ->
    (file name, page name, what was read)."""
    found, sheets = {}, {}
    for path in sorted(board.glob(f"{project}-*.kicad_sch")):
        page = by_file.get(path.name)
        if page is None:
            continue
        src = path.read_text()
        for start, end in symbol_blocks(src):
            sym = read_symbol(src[start:end])
            if sym["uuid"]:
                found[sym["uuid"]] = (path.name, page, sym)
        for start, end in sheet_blocks(src):
            sh = read_sheet(src[start:end])
            if sh["uuid"]:
                sheets[sh["uuid"]] = (path.name, page, sh)
    return found, sheets


def next_ref_free(con, prefix, taken):
    n = 1
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


# ------------------------------------------------- the library fields (T2.11)

FIELDS = ["Value", "Footprint", "Description", "Datasheet", "Manufacturer",
          "MPN", "note", "ipn", "checked"]

PULLED = {"Description": ("parts_table", "description"),
          "Value": ("parts_table", "value"),
          "Footprint": ("parts_table", "footprint"),
          "note": ("parts_table", "note"),
          "Manufacturer": ("parts_table", "manufacturer"),
          "Datasheet": ("parts_table", "datasheet")}


def field_rows(con, nickname):
    """One dict of T2.11 fields per part whose symbol is in this project's
    library, keyed by the symbol name."""
    out = {}
    for ipn, description, value, footprint, note, symbol, mpn, \
            manufacturer, datasheet, checked in con.execute(
            "select ipn, description, value, footprint, note, symbol, mpn, "
            "manufacturer, datasheet, checked from parts_table "
            "where symbol is not null"):
        nick, _, name = symbol.partition(":")
        if nick != nickname:
            continue
        out[name] = {"ipn": ipn,
                     "Value": value or ipn,
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
                   ("note", "note"), ("parent", "parent_field"),
                   ("checked", "checked"))


def push_instances(con, board, project, root, root_src, rows):
    """Record to sheets: T2.11's fields onto every placed instance the
    record knows (n4.3), and the per-instance block - every path the
    drawing stands for, with its reference. Positions, wiring and graphics
    untouched. Returns {sheet file: instances rewritten}."""
    draw = {d["uuid"]: d for d in drawings(rows)}
    sheets = root_sheets(root_src)
    starts = root_page_of(rows)
    placed, placed_sheets = read_pages(board, project,
                                       page_files(con, project, root_src))
    by_file = {}
    for u, (fname, page, sym) in placed.items():
        if u in draw:
            by_file.setdefault(fname, set()).add(u)
    for u, (fname, page, sh) in placed_sheets.items():
        if u in draw:
            by_file.setdefault(fname, set()).add(u)
    changed = {}
    for fname, wanted in sorted(by_file.items()):
        path = board / fname
        before = src = path.read_text()
        out, last, count = [], 0, 0
        edits = []
        for start, end in symbol_blocks(src):
            block = src[start:end]
            u = read_symbol(block)["uuid"]
            if u not in wanted:
                continue
            row = draw[u]
            new = block
            for field, key in INSTANCE_FIELDS:
                new = set_property(new, field, row[key])
            new = set_property(new, "Reference", row["ref"])
            new = set_instances(
                new, project, row,
                lambda p, row=row: kicad_path(root, sheets, starts,
                                              row["page"], p))
            if new != block:
                edits.append((start, end, new))
        for start, end in sheet_blocks(src):
            block = src[start:end]
            u = read_sheet(block)["uuid"]
            if u not in wanted:
                continue
            new = set_property(block, "Sheetname", draw[u]["ref"])
            if new != block:
                edits.append((start, end, new))
        for start, end, new in sorted(edits):
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


LABEL_RE = re.compile(r'\n\t\((?:global_label|hierarchical_label|label) '
                      r'"(?:[^"\\]|\\.)*"\n(?:.*\n)*?\t\)')


def push_labels(con, board, project, root, root_src, rows, model):
    """Record to sheets, the nets: every label on the page is deleted,
    then every net_table row is written back as a label at its pin, of the
    kind the net model gives it. Sheet symbols
    get their pins, stubs and labels afresh. Each bus that leaves a page
    gets its hierarchical label once. Labels elsewhere on the page are not
    touched. Returns {sheet file: labels written}."""
    draw = {d["uuid"]: d for d in drawings(rows)}
    placed, placed_sheets = read_pages(board, project,
                                       page_files(con, project, root_src))
    by_file = {}
    for u, (fname, page, sym) in placed.items():
        if u in draw:
            by_file.setdefault(fname, (page, set()))[1].add(u)
    for u, (fname, page, sh) in placed_sheets.items():
        if u in draw:
            by_file.setdefault(fname, (page, set()))[1].add(u)
    changed = {}
    for fname, (page, wanted) in sorted(by_file.items()):
        path = board / fname
        before = src = path.read_text()
        lib_ids = {draw[u]["symbol"] for u in wanted if not draw[u]["sheet"]}
        blocks = library_blocks(board, project, lib_ids)
        ends, fresh, gone = set(), "", set()
        for start, end in symbol_blocks(src):
            block = src[start:end]
            sym = read_symbol(block)
            u = sym["uuid"]
            if u not in wanted:
                continue
            at = re.search(r'\n\t\t\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)', block)
            x, y, rot = float(at.group(1)), float(at.group(2)), int(float(at.group(3)))
            row = draw[u]
            pe = pin_ends(blocks[row["symbol"]], sym["unit"], x, y, rot)
            ends.update((px, py) for px, py, _ in pe.values())
            pins = model.pins_for(row)
            fresh += "".join(label_sexp(pins[n], *pe[n], model.kind(pins[n], page))
                             for n in sorted(pins) if n in pe)
        edits = []
        for start, end in sheet_blocks(src):
            block = src[start:end]
            sh = read_sheet(block)
            u = sh["uuid"]
            if u not in wanted:
                continue
            row = draw[u]
            pins = model.sheet_pins(row["sheet"])
            for name in set(sh["pins"]) | set(pins):
                gone.add(uid("stub", u, name))
                gone.add(uid("pinlabel", u, name))
            new = set_sheet_pins(block, pins, u)
            if new != block:
                edits.append((start, end, new))
            x, y = sh["at"]
            # `u` is the sheet symbol's own uuid on the page; the record
            # keys its nets by the node's id - T2.4
            named = model.pins.get(row["id"]) or {}
            fresh += pin_fittings_sexp(
                u, x, y, sh["size"][0], pins, named,
                {n: model.kind(net, page) for n, net in named.items()})
        if edits:
            out, last = [], 0
            for start, end, new in sorted(edits):
                out.append(src[last:start]); out.append(new); last = end
            out.append(src[last:])
            src = "".join(out)

        # every label on the page goes, wherever it sits: the record is the
        # only source of a net, so a label is the tool's to write afresh.
        # A symbol moved by hand leaves nothing behind
        src = LABEL_RE.sub("", src)
        src = remove_by_uuid(src, gone)
        src = remove_matching(src, fresh)
        src, buses = port_area(src, page, model)
        fresh += buses
        if fresh:
            close = src.rstrip().rfind(")")
            src = src[:close] + fresh + src[close:]
        if src != before:
            path.write_text(src)
            run = subprocess.run(["kicad-cli", "sch", "upgrade", "--force",
                                  str(path)], capture_output=True, text=True)
            if run.returncode != 0:
                path.write_text(before)
                raise Bad(f"kicad-cli could not normalize {path}: "
                          + (run.stderr or run.stdout).strip())
            changed[fname] = (fresh.count("(label") + fresh.count("(hierarchical_label"))
    return changed


def port_area(src, page, model):
    """The page's port area: every port and every bus breakout, one column
    at the top right, another column to the left when the first is full.
    Tool-owned: what an earlier run drew is removed, by uuid and by
    geometry, and the area is drawn afresh. Returns (src, text to add)."""
    buses = model.buses(page)
    ports = model.leaving(page)
    old_buses = set()
    for a, b in top_blocks(src, "(hierarchical_label"):
        m = re.match(r'\t\(hierarchical_label "\{([^}"]*)\}"', src[a:b])
        u = re.search(r'\(uuid "([^"]+)"\)', src[a:b])
        if m and u and u.group(1) == uid("bus", page, m.group(1)):
            old_buses.add(m.group(1))
    candidates = set(model.files) | set(model.bus_of)
    gone = set()
    for net in candidates:
        gone.update({uid("port", page, net), uid("portstub", page, net),
                     uid("portlabel", page, net)})
    for bus in old_buses | set(buses) | set(model.members):
        gone.update({uid("bus", page, bus), uid("busstub", page, bus)})
        for m in candidates:
            gone.update({uid("busentry", page, bus, m),
                         uid("buswire", page, bus, m),
                         uid("buslabel", page, bus, m)})
    src = remove_by_uuid(src, gone)
    width, height = PAPERS.get(paper_of(src), PAPERS["A"])
    x, y, top = snap(width - MARGIN - 12 * GRID), TOP, TOP
    limit = height - MARGIN
    body = ""
    # a drawing that reaches into the port area would touch its labels.
    # Reported: the page was laid out before the port area had room, and
    # placing it afresh gives it room
    reach = max([float(m) for m in re.findall(r'\n\t\t\(at (-?[\d.]+) ', src)]
                + [0.0])
    if reach > x - 4 * GRID and (ports or buses):
        print(f"    {page}: a symbol sits at x {reach:.1f}, inside the port "
              f"area from x {x - 4 * GRID:.1f}. Labels may touch. Place "
              "the page afresh")

    def place(h):
        nonlocal x, y
        if y + h > limit and y > top:
            x, y = snap(x - 20 * GRID), top
        at = (x, y)
        y = snap(y + h)
        return at

    for net in ports:
        body += port_sexp(page, net, *place(2 * GRID))
    for bus in buses:
        members = [m for m in model.members.get(bus, [])
                   if page in model.files.get(m, ())]
        body += bus_breakout_sexp(page, bus, members,
                                  *place((len(members) + 3) * GRID))
    return remove_matching(src, body), body


# ------------------------------------------------------------ simulation

SIM_FIELDS = ("Sim.Device", "Sim.Type", "Sim.Params", "Sim.Library",
              "Sim.Name", "Sim.Pins")
SIM_SYMBOLS = ("VDC", "VSIN")       # copied from Simulation_SPICE, T2.6e
SIM_REF = re.compile(r"^VS\d+$")    # a source the tool drew
FOUR_KT = 4 * 1.380649e-23 * 300    # V^2/Hz per ohm at 300 K
PASSIVE_CLASS = {"R": "R", "C": "C", "L": "L"}

# KiCad's single-pole op-amp, Simulation_SPICE.sp, public domain, Holger
# Vogt. Copied in so the models file needs nothing outside the project
OPAMP_SUBCKT = """\
* single-pole op-amp, after kicad_builtin_opamp (Holger Vogt, public domain)
* POLE the open-loop pole, GAIN the open-loop gain, ROUT the output resistance
.subckt bb_opamp in+ in- vcc vee out params: POLE=20 GAIN=20k ROUT=10
  G10 0 int in+ in- 100u
  R1 int 0 {GAIN/100u}
  C1 int 0 {1/(6.28*(GAIN/100u)*POLE)}
  Eout 2 0 int 0 1
  Rout 2 out {ROUT}
  Elow lee 0 vee 0 1
  Ehigh lcc 0 vcc 0 1
  Dlow lee int Dlimit
  Dhigh int lcc Dlimit
  .model Dlimit D N=0.01
.ends
"""

VALUE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([pnumkMG])?(?:Ohm|ohm|F|H|R|Ω)?\b")


def spice_value(value):
    """`1.13 kOhm` to `1.13k`, `100 pF` to `100p`, `6.8 uH` to `6.8u`. None
    when the value does not start with a number."""
    m = VALUE_RE.match(value or "")
    if not m:
        return None
    return m.group(1) + (m.group(2) or "")


def del_property(block, name):
    """Remove one field from a symbol block, if it carries it."""
    pat = re.compile(r'\n\t\t\(property "%s" "(?:[^"\\]|\\.)*"\n(?:.*\n)*?\t\t\)'
                     % re.escape(name))
    return pat.sub("", block, count=1)


def set_exclude(block, yes):
    return re.sub(r"\(exclude_from_sim (?:yes|no)\)",
                  f"(exclude_from_sim {'yes' if yes else 'no'})", block,
                  count=1)


def sim_params_of(text):
    """`gbw=1e9 aol=1e5 en=1e-9 rout=10` as floats."""
    out = {}
    for tok in (text or "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                out[k.strip().lower()] = float(v)
            except ValueError:
                raise Bad(f"sim_params: '{tok}' is not <name>=<number>")
    return out


def opamp_pinmap(pins):
    """The op-amp's channels from its part-file pin names. Returns
    (channels, vplus, vminus): channels {key: {'in+', 'in-', 'out'}} of
    pin numbers. Names read `OUT_A` `IN_A+` `IN_A-` `V+` `V-`, or `VOUT1`
    `+IN1` `-IN1` `+VS` `-VS`, or `OUT1` `IN1+` `IN1-` `VCC` `GND`."""
    chans, vplus, vminus = {}, None, None
    for number, name, *_ in pins:
        n = str(name).upper().replace("–", "-").replace("−", "-")
        n = n.replace("_", "")
        if n in ("V+", "+VS", "VS+", "VCC", "VDD", "VS", "+V", "VP"):
            vplus = str(number); continue
        if n in ("V-", "-VS", "VS-", "VEE", "GND", "VSS", "-V", "VN"):
            vminus = str(number); continue
        m = re.match(r"^V?OUT([A-Z0-9]*)$", n)
        if m:
            chans.setdefault(m.group(1), {})["out"] = str(number); continue
        m = re.match(r"^([+-])?IN([A-Z0-9]*)([+-])?$", n)
        if m and (m.group(1) or m.group(3)) and not (m.group(1) and m.group(3)):
            sign = m.group(1) or m.group(3)
            chans.setdefault(m.group(2), {})["in" + sign] = str(number)
            continue
    bad = [k for k, c in chans.items() if set(c) != {"in+", "in-", "out"}]
    if bad or not chans or vplus is None or vminus is None:
        return None
    return chans, vplus, vminus


def subckt_name(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name or "part")


def write_models(con, board, project):
    """`models/<project>.sp` from every `opamp` part: one subckt per part,
    nodes in pin-number order. Returns {ipn: (subckt, Sim.Pins)}."""
    models, body = {}, OPAMP_SUBCKT
    for ipn, name, params in con.execute(
            "select ipn, name, sim_params from parts_table "
            "where sim_model = 'opamp' order by ipn"):
        path = board / "parts" / f"{ipn}-{name}.json"
        if not path.exists():
            raise Bad(f"{ipn} {name}: sim_model opamp but no part file "
                      f"{path.name}; datasheet-read first")
        pins = json.loads(path.read_text()).get("pins") or []
        mapped = opamp_pinmap(pins)
        if mapped is None:
            raise Bad(f"{ipn} {name}: pin names do not read as an op-amp's "
                      "(in+, in-, out per channel, V+, V-): "
                      + " ".join(str(p[1]) for p in pins))
        chans, vplus, vminus = mapped
        p = sim_params_of(params)
        for key in ("gbw", "aol", "en", "rout"):
            if key not in p:
                raise Bad(f"{ipn} {name}: sim_params lacks {key}=")
        pole = p["gbw"] / p["aol"]
        rnoise = p["en"] ** 2 / FOUR_KT
        sub = subckt_name(name)
        numbers = [str(pin[0]) for pin in pins]
        nodes = [f"n{n}" for n in numbers]
        body += (f"\n* {name}, {ipn}: {len(chans)} channel(s), gbw {p['gbw']:g}"
                 f" aol {p['aol']:g} en {p['en']:g} rout {p['rout']:g}\n"
                 f".subckt {sub} {' '.join(nodes)}\n")
        for key in sorted(chans):
            c = chans[key]
            tag = key or "1"
            # the noise: a resistor to ground whose thermal noise, 4kTR
            # = en^2, is added to in+ through a unity VCVS, so the signal
            # path sees no resistance and .noise sees the source
            body += (f"  Rn{tag} z{tag} 0 {rnoise:.6g}\n"
                     f"  En{tag} x{tag} n{c['in+']} z{tag} 0 1\n"
                     f"  X{tag} x{tag} n{c['in-']} n{vplus} n{vminus} "
                     f"n{c['out']} bb_opamp POLE={pole:.6g} GAIN={p['aol']:g}"
                     f" ROUT={p['rout']:g}\n")
        body += ".ends\n"
        models[ipn] = (sub, " ".join(f"{n}=n{n}" for n in numbers))
    # rnet: a resistor between named pin pairs, every other pin open. A
    # switch in one position, a jumper, a pad. sim_params
    # `r=<ohm> pins=<a>:<b>,<c>:<d>`, names from the part file
    for ipn, name, params in con.execute(
            "select ipn, name, sim_params from parts_table "
            "where sim_model = 'rnet' order by ipn"):
        path = board / "parts" / f"{ipn}-{name}.json"
        if not path.exists():
            raise Bad(f"{ipn} {name}: sim_model rnet but no part file "
                      f"{path.name}")
        pins = json.loads(path.read_text()).get("pins") or []
        by_name = {str(p[1]): str(p[0]) for p in pins}
        p = {}
        for tok in (params or "").split():
            k, _, v = tok.partition("=")
            p[k.lower()] = v
        if "r" not in p or "pins" not in p:
            raise Bad(f"{ipn} {name}: sim_params wants r=<ohm> pins=<a>:<b>,...")
        pairs = []
        for pair in p["pins"].split(","):
            a, _, b = pair.partition(":")
            if a not in by_name or b not in by_name:
                raise Bad(f"{ipn} {name}: sim_params pins names {a}:{b}, "
                          "not pins of the part file")
            pairs.append((by_name[a], by_name[b]))
        sub = subckt_name(name)
        numbers = [str(pin[0]) for pin in pins]
        body += (f"\n* {name}, {ipn}: resistor network, {p['r']} ohm between "
                 f"{p['pins']}\n"
                 f".subckt {sub} {' '.join(f'n{n}' for n in numbers)}\n")
        for i, (a, b) in enumerate(pairs, 1):
            body += f"  R{i} n{a} n{b} {p['r']}\n"
        # a pin in no pair leaks to ground through 1 G: KiCad's simulator
        # adds `.probe alli`, a current probe on every terminal, and a
        # floating subcircuit pin then makes the matrix singular (n2.5)
        used = {a for a, _ in pairs} | {b for _, b in pairs}
        for n in numbers:
            if n not in used:
                body += f"  Rx{n} n{n} 0 1G\n"
        body += ".ends\n"
        models[ipn] = (sub, " ".join(f"{n}=n{n}" for n in numbers))
    folder = board / "models"
    folder.mkdir(exist_ok=True)
    path = folder / f"{project}.sp"
    if not path.exists() or path.read_text() != body:
        path.write_text(body)
    return models


def active_sim(con):
    """The one simulation instance, or None. Two is a refusal."""
    rows = con.execute("select name, kind, directive from sim_table "
                       "order by name").fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise Bad("more than one simulation in sim_table: "
                  + " ".join(r[0] for r in rows)
                  + ". One is pushed at a time; `table-write sim drop` the "
                  "others")
    name, kind, directive = rows[0]
    blocks = [b for (b,) in con.execute(
        "select block from sim_net_table where name = ? and block != '' "
        "order by block", (name,))]
    sources = [(n, s) for n, s in con.execute(
        "select net, source from sim_net_table where name = ? and net != ''"
        " and source is not null order by net", (name,))]
    return {"name": name, "kind": kind, "directive": directive or "",
            "blocks": blocks, "sources": sources}


def sim_inside(con, blocks):
    """Every part id under the block references, walking `parent`. The chain
    crosses rooms; rooms are walked through but are not instances."""
    roots = []
    for bl in blocks:
        row = con.execute("select id from ref_table where ref = ? "
                          "and kind = 'part'", (bl,)).fetchone()
        if row is None:
            raise Bad(f"simulation block {bl} names no instance")
        roots.append(row[0])
    seen, frontier = set(roots), list(roots)
    while frontier:
        marks = ",".join("?" * len(frontier))
        kids = [i for (i,) in con.execute(
            f"select id from ref_table where parent in ({marks})",
            frontier) if i not in seen]
        seen.update(kids)
        frontier = kids
    parts = {i for (i,) in con.execute(
        "select id from ref_table where kind = 'part'")}
    return seen & parts


def sim_fields_for(row, part, models, project):
    """The Sim.* fields an instance inside the blocks carries, or None
    with a reason when it has no model."""
    model = part["sim_model"] or PASSIVE_CLASS.get(row["ipn"][0])
    if model in ("R", "C", "L"):
        value = part["sim_params"] or spice_value(part["value"])
        if not value:
            return None, f"value {part['value']!r} is not a spice value"
        return {"Sim.Device": model, "Sim.Params": value}, ""
    if model in ("opamp", "rnet"):
        if row["ipn"] not in models:
            return None, "no model written"
        sub, pins = models[row["ipn"]]
        return {"Sim.Device": "SUBCKT",
                "Sim.Library": f"${{KIPRJMOD}}/models/{project}.sp",
                "Sim.Name": sub, "Sim.Pins": pins}, ""
    return None, ""


def push_sim_fields(con, board, project, root_src, rows, sim, models):
    """Every placed instance: Sim.* fields and exclude_from_sim per the
    active simulation. Returns ({file: symbols rewritten}, [skipped])."""
    inside = sim_inside(con, sim["blocks"]) if sim else set()
    parts = {}
    for ipn, model, params, value, description in con.execute(
            "select ipn, sim_model, sim_params, value, description "
            "from parts_table"):
        parts[ipn] = {"sim_model": model, "sim_params": params,
                      "value": value_of(con, ipn, description)}
    by_uuid = {r["uuid"]: r for r in rows}
    placed, _ = read_pages(board, project, page_files(con, project, root_src))
    by_file = {}
    for u, (fname, page, sym) in placed.items():
        if u in by_uuid:
            by_file.setdefault(fname, set()).add(u)
    changed, skipped = {}, []
    for fname, wanted in sorted(by_file.items()):
        path = board / fname
        before = src = path.read_text()
        edits = []
        for start, end in symbol_blocks(src):
            block = src[start:end]
            u = read_symbol(block)["uuid"]
            if u not in wanted:
                continue
            row = by_uuid[u]
            part = parts[row["ipn"]]
            new = block
            in_sim = row.get("id") in inside
            fields, why = (sim_fields_for(row, part, models, project)
                           if in_sim else (None, ""))
            if fields:
                for name in SIM_FIELDS:
                    new = (set_property(new, name, fields[name])
                           if name in fields else del_property(new, name))
                new = set_exclude(new, False)
            else:
                if in_sim and why:
                    skipped.append(f"{row['ref']}: {why}")
                for name in SIM_FIELDS:
                    new = del_property(new, name)
                # the exporter writes a junk line, `J6 __J6`, for any
                # symbol with no model and no exclusion (n2.2): with a
                # simulation active, everything without fields is excluded
                new = set_exclude(new, bool(sim))
            if new != block:
                edits.append((start, end, new))
        if edits:
            out, last = [], 0
            for start, end, new in sorted(edits):
                out.append(src[last:start]); out.append(new); last = end
            out.append(src[last:])
            path.write_text("".join(out))
            run = subprocess.run(["kicad-cli", "sch", "upgrade", "--force",
                                  str(path)], capture_output=True, text=True)
            if run.returncode != 0:
                path.write_text(before)
                raise Bad(f"kicad-cli could not normalize {path}: "
                          + (run.stderr or run.stdout).strip())
            changed[fname] = len(edits)
    return changed, skipped


def ensure_sim_symbols(board, project):
    """VDC and VSIN in the project library, copied from Simulation_SPICE
    once (section 3.3: nothing points outside the repository)."""
    library = board / "lib" / f"{project}.kicad_sym"
    src = library.read_text()
    copied = []
    for name in SIM_SYMBOLS:
        if copy_part.top_level(src, name) is None:
            copy_part.copy(library, project,
                           {"library": "Simulation_SPICE", "symbol": name},
                           name, None)
            copied.append(name)
    if copied:
        normalize_lib(library)
    return copied


def source_sexp(project, path_uuid, ref, net, source, lib_id, x, y, u):
    """One source symbol at (x, y), pin 1 up, and its two labels."""
    kind, _, amount = source.partition(" ")
    kind = kind.lower()
    try:
        amount = f"{float(amount):g}"
    except ValueError:
        raise Bad(f"source '{source}' on {net}: wants `dc <V>` or `ac <V>`")
    if kind == "dc":
        typ, params = "DC", f"dc={amount}"
    elif kind == "ac":
        typ, params = "SIN", f"dc=0 ampl={amount} f=1k ac={amount}"
    else:
        raise Bad(f"source '{source}' on {net}: wants `dc <V>` or `ac <V>`")
    body = (
        "\t(symbol\n"
        f"\t\t(lib_id \"{lib_id}\")\n"
        f"\t\t(at {x:.2f} {y:.2f} 0)\n"
        "\t\t(unit 1)\n"
        "\t\t(exclude_from_sim no)\n\t\t(in_bom no)\n\t\t(on_board no)\n"
        "\t\t(dnp no)\n\t\t(fields_autoplaced yes)\n"
        f"\t\t(uuid \"{u}\")\n"
        + property_sexp("Reference", ref, f"{x + 2 * GRID:.2f}",
                        f"{y - GRID / 2:.2f}", justify="left")
        + property_sexp("Value", f"{net} {source}", f"{x + 2 * GRID:.2f}",
                        f"{y + GRID / 2:.2f}", justify="left")
        + property_sexp("Sim.Device", "V", f"{x:.2f}", f"{y:.2f}", hide=True)
        + property_sexp("Sim.Type", typ, f"{x:.2f}", f"{y:.2f}", hide=True)
        + property_sexp("Sim.Pins", "1=+ 2=-", f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        + property_sexp("Sim.Params", params, f"{x:.2f}", f"{y:.2f}",
                        hide=True)
        # pin uuids written here, so a redraw is byte-identical and a
        # second push changes nothing
        + f"\t\t(pin \"1\"\n\t\t\t(uuid \"{uid('simpin', u, '1')}\")\n\t\t)\n"
        + f"\t\t(pin \"2\"\n\t\t\t(uuid \"{uid('simpin', u, '2')}\")\n\t\t)\n"
        + "\t\t(instances\n"
        f"\t\t\t(project \"{project}\"\n"
        f"\t\t\t\t(path \"{path_uuid}\"\n"
        f"\t\t\t\t\t(reference \"{ref}\")\n"
        "\t\t\t\t\t(unit 1)\n"
        "\t\t\t\t)\n\t\t\t)\n\t\t)\n"
        "\t)\n"
    )
    return body


def directive_sexp(text, x, y, u):
    return (
        f"\t(text \"{esc(text)}\"\n"
        "\t\t(exclude_from_sim no)\n"
        f"\t\t(at {x:.2f} {y:.2f} 0)\n"
        f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {FONT} {FONT})\n\t\t\t)\n"
        "\t\t\t(justify left bottom)\n\t\t)\n"
        f"\t\t(uuid \"{u}\")\n"
        "\t)\n"
    )


def remove_sim_fittings(src, project):
    """Drop every source the tool drew, by lib_id and reference, the
    labels at its pins, and the sim room's box, name and directive."""
    gone = set()
    for start, end in symbol_blocks(src):
        sym = read_symbol(src[start:end])
        if sym["lib_id"] in {f"{project}:{n}" for n in SIM_SYMBOLS} \
                and SIM_REF.match(sym["props"].get("Reference", "")):
            gone.add(sym["uuid"])
            at = re.search(r'\n\t\t\(at (-?[\d.]+) (-?[\d.]+)', src[start:end])
            x, y = float(at.group(1)), float(at.group(2))
            gone.add(uid("simlabel", sym["uuid"], "1"))
            gone.add(uid("simlabel", sym["uuid"], "2"))
    spans = []
    for a, b in top_blocks(src, "(symbol"):
        u = re.search(r'\n\t\t\(uuid "([^"]+)"\)', src[a:b])
        if u and u.group(1) in gone:
            spans.append((a, b))
    for head in ("(rectangle", "(text"):
        for a, b in top_blocks(src, head):
            u = re.search(r'\(uuid "([^"]+)"\)', src[a:b])
            if u and u.group(1).startswith(SIM_UUID_MARK):
                spans.append((a, b))
    for a, b in sorted(spans, reverse=True):
        src = src[:a] + src[b:]
    return remove_by_uuid(src, gone)


SIM_UUID_MARK = "5133"   # the tool's sim-room fittings: uuids that start so


def sim_uid(*parts):
    return SIM_UUID_MARK + uid(*parts)[len(SIM_UUID_MARK):]


def push_sim_fittings(con, board, project, root, root_src, rows, sim):
    """The sim room on the block's page: a source per source row, its
    labels, the directive. Every page loses what an earlier push drew;
    the active instance's page gets it afresh. Returns {file: sources}."""
    sheets = root_sheets(root_src)
    starts = root_page_of(rows)
    by_file = page_files(con, project, root_src)
    page = None
    if sim and sim["blocks"]:
        row = con.execute("select page, '' from ref_table where ref = ? "
                          "and kind = 'part'",
                          (sim["blocks"][0],)).fetchone()
        page = (row[0] or "").strip() if row else None
        if not page:
            raise Bad(f"simulation block {sim['blocks'][0]} has no page")
    changed = {}
    for path in sorted(board.glob(f"{project}-*.kicad_sch")):
        pg = by_file.get(path.name)
        if pg is None:
            continue
        before = src = path.read_text()
        src = remove_sim_fittings(src, project)
        count = 0
        if sim and pg == page and (sim["sources"] or sim["directive"]):
            lib_ids = {f"{project}:{n}" for n in SIM_SYMBOLS}
            blocks = library_blocks(board, project, lib_ids)
            have = set(re.findall(r'\t\t\(symbol "([^"]+)"', src))
            missing = "".join(indent_block(blocks[k], 1).rstrip() + "\n"
                              for k in sorted(lib_ids) if k not in have)
            if missing:
                m = re.search(r"\(lib_symbols\n", src)
                src = src[:m.end()] + missing + src[m.end():]
            paper = paper_of(src)
            width, height = PAPERS.get(paper, PAPERS["A"])
            x0 = MARGIN + 2 * GRID
            y0 = snap(lowest_used(src) + 10 * GRID)
            path_uuid = kicad_path(root, sheets, starts, page, "")
            body, x = "", x0 + 2 * GRID
            for i, (net, source) in enumerate(sim["sources"], 1):
                lib = f"{project}:{'VDC' if source.lower().startswith('dc') else 'VSIN'}"
                u = sim_uid("simsrc", page, net)
                sy = y0 + 4 * GRID
                body += source_sexp(project, path_uuid, f"VS{i}", net, source,
                                    lib, x, sy, u)
                ends = pin_ends(blocks[lib], 1, x, sy, 0)
                body += label_sexp(net, *ends["1"], "label",
                                   uid("simlabel", u, "1"))
                body += label_sexp("GND", *ends["2"], "label",
                                   uid("simlabel", u, "2"))
                x += 10 * GRID
                count += 1
            w = max(x - x0, 16 * GRID)
            h = 9 * GRID
            body += (
                "\t(rectangle\n"
                f"\t\t(start {x0:.2f} {y0:.2f})\n"
                f"\t\t(end {x0 + w:.2f} {y0 + h:.2f})\n"
                "\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type dash)\n\t\t)\n"
                "\t\t(fill\n\t\t\t(type none)\n\t\t)\n"
                f"\t\t(uuid \"{sim_uid('simbox', page)}\")\n"
                "\t)\n"
                + directive_sexp(f"sim {sim['name']} {sim['kind']}",
                                 x0 + GRID / 2, y0 - GRID / 2,
                                 sim_uid("simname", page))
                + directive_sexp(sim["directive"], x0 + GRID,
                                 y0 + h - GRID / 2,
                                 sim_uid("simdirective", page)))
            for size in PAPER_ORDER:
                pw, ph = PAPERS[size]
                if y0 + h + MARGIN <= ph and (pw, ph) >= (width, height):
                    if size != paper:
                        src = re.sub(r'\(paper "[^"]*"\)', f'(paper "{size}")',
                                     src, count=1)
                    break
            close = src.rstrip().rfind(")")
            src = src[:close] + body + src[close:]
        if src != before:
            path.write_text(src)
            run = subprocess.run(["kicad-cli", "sch", "upgrade", "--force",
                                  str(path)], capture_output=True, text=True)
            if run.returncode != 0:
                path.write_text(before)
                raise Bad(f"kicad-cli could not normalize {path}: "
                          + (run.stderr or run.stdout).strip())
            changed[path.name] = count
    return changed


def is_sim_fitting(project, sym):
    """A source symbol the tool drew: not the record's, skipped by place."""
    return (sym["lib_id"] in {f"{project}:{n}" for n in SIM_SYMBOLS}
            and bool(SIM_REF.match(sym["props"].get("Reference", ""))))


def push_board(board, project, root, root_src, rows):
    """Record to board: every footprint whose Reference the record holds
    takes that instance's sheet path, so Update PCB from Schematic finds
    it where the symbol now is. Placement and routing untouched. Returns
    (rewritten, unknown references)."""
    path = Path(board) / f"{project}.kicad_pcb"
    if not path.exists():
        return 0, []
    sheets = root_sheets(root_src)
    starts = root_page_of(rows)
    by_ref = {}
    for r in rows:
        if r["ref"] and r["page"] and not r["sheet"]:
            by_ref.setdefault(r["ref"], r)
    before = src = path.read_text()
    out, last, count, unknown = [], 0, 0, []
    for a, b in top_blocks(src, "(footprint"):
        block = src[a:b]
        ref = re.search(r'\n\t\t\(property "Reference" "((?:[^"\\]|\\.)*)"',
                        block)
        if not ref:
            continue
        row = by_ref.get(unesc(ref.group(1)))
        if row is None:
            unknown.append(unesc(ref.group(1)))
            continue
        try:
            kp = kicad_path(root, sheets, starts, row["page"], row["path"])
        except Bad:
            continue
        # the board omits the root: /<page sheet>/<sub-sheets>/<symbol>
        want = kp[len(f"/{root}"):] + f"/{row['uuid']}"
        new = re.sub(r'\n\t\t\(path "[^"]*"\)', f'\n\t\t(path "{want}")',
                     block, count=1)
        if new != block:
            out.append(src[last:a]); out.append(new); last = b
            count += 1
    if count:
        out.append(src[last:])
        path.write_text("".join(out))
        run = subprocess.run(["kicad-cli", "pcb", "upgrade", "--force",
                              str(path)], capture_output=True, text=True)
        if run.returncode != 0:
            path.write_text(before)
            raise Bad(f"kicad-cli could not normalize {path}: "
                      + (run.stderr or run.stdout).strip())
    return count, sorted(set(unknown))


def pull_positions(con, board, project, root_src):
    """Pages to record: the place of every symbol and sheet symbol the
    record knows that is not where the record has it, marked `hand`. One
    where the tool left it is not written. Returns (written, moved)."""
    placed, placed_sheets = read_pages(board, project,
                                       page_files(con, project, root_src))
    known = {(u, p): (x, y, r) for u, p, x, y, r in con.execute(
        "select id, path, x, y, rot from ref_table")}
    hand = 0
    by_uuid = {}
    for (u, p), v in known.items():
        by_uuid.setdefault(u, []).append((p, v))
    written = moved = 0
    def put(u, x, y, rot):
        nonlocal written, moved
        if u not in by_uuid:
            return
        for p, (ox, oy, orot) in by_uuid[u]:
            if ox is None or abs(ox - x) > 0.01 or abs(oy - y) > 0.01 \
                    or (orot or 0) != rot:
                if ox is not None:
                    moved += 1
                # not where the tool left it: the hand's place, locked
                con.execute("update ref_table set x = ?, y = ?, rot = ?, "
                            "placed = 'hand' where uuid = ? and path = ?",
                            (x, y, rot, u, p))
                written += 1
    for u, (fname, page, sym) in placed.items():
        pass
    for path in sorted(board.glob(f"{project}-*.kicad_sch")):
        src = path.read_text()
        for a, b in symbol_blocks(src):
            sym = read_symbol(src[a:b])
            at = re.search(r'\n\t\t\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)', src[a:b])
            if sym["uuid"] and at:
                put(sym["uuid"], float(at.group(1)), float(at.group(2)),
                    int(float(at.group(3))) % 360)
        for a, b in sheet_blocks(src):
            sh = read_sheet(src[a:b])
            if sh["uuid"]:
                put(sh["uuid"], sh["at"][0], sh["at"][1], 0)
    con.commit()
    return written, moved


def bus_alias_sexp(model):
    """The bus aliases as schematic blocks, `(bus_alias "NAME" (members
    ...))`, in name order."""
    out = ""
    for bus, members in sorted(model.aliases().items()):
        names = " ".join(f'"{esc(m)}"' for m in members)
        out += (f'\t(bus_alias "{esc(bus)}"\n'
                f"\t\t(members {names})\n"
                "\t)\n")
    return out


def write_bus_aliases(board, project, model):
    """The bus aliases into every sheet file, after the paper line.

    KiCad 10 keeps them in the schematic, not the project: the GUI clears
    `schematic.bus_aliases` in the `.kicad_pro` on open and does not read
    it, so a bus there never expands and every rail and ground stays
    page-local (n2.6). This runs LAST in a run: `kicad-cli sch upgrade`
    drops the token, so nothing may normalize a sheet after it."""
    body = bus_alias_sexp(model)
    changed = 0
    for path in sorted(Path(board).glob(f"{project}*.kicad_sch")):
        before = src = path.read_text()
        for a, b in sorted(top_blocks(src, "(bus_alias"), reverse=True):
            src = src[:a] + src[b:]
        if body:
            m = re.search(r'\n\t\(paper "[^"]*"\)\n', src)
            if not m:
                raise Bad(f"{path.name} carries no paper line")
            src = src[:m.end()] + body + src[m.end():]
        if src != before:
            path.write_text(src)
            changed += 1
    # one home. The project key held a second copy that went stale and
    # that the GUI clears anyway: emptied here so nothing reads it
    pro = Path(board) / f"{project}.kicad_pro"
    if pro.exists():
        data = json.loads(pro.read_text())
        sch = data.setdefault("schematic", {})
        if sch.get("bus_aliases"):
            sch["bus_aliases"] = {}
            pro.write_text(json.dumps(data, indent=2) + "\n")
    return changed


def pull_fields(con, library, nickname):
    """Library to record: Description, Value, Footprint, note, Manufacturer,
    Datasheet to `parts_table`. MPN is reported on mismatch, never
    written."""
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


def size_of(row, blocks, model=None):
    """The space one symbol needs, its own drawing plus the clearance that
    keeps a neighbour's pins off it."""
    half_w, half_h, _, _ = geom(row, blocks, model)
    return 2 * half_w, 2 * (half_h + GRID)


def geom(row, blocks, model=None):
    """(half width, half height, bottom, right) of a drawing: a symbol from
    its block, a sheet symbol from its pin count plus room for the labels
    off its right edge."""
    if row.get("sheet"):
        n = len(model.sheet_pins(row["sheet"])) if model else 0
        w = SHEET_W + STUB + 10 * GRID
        h = sheet_height(n)
        return w / 2, h / 2, h / 2, w / 2
    block = blocks[row["symbol"]]
    half_w, half_h = extent(block)
    bottom, right = edges(block)
    return half_w, half_h, bottom, right


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


def family_templates(rows):
    """The arrangement of every family whose parent has a place: keyed by
    the parent's part, a list of (child part, dx, dy, rot) in child
    reference order, offsets from the parent's origin. A first family
    with a given parent part is the template; later ones do not replace
    it."""
    by_page = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(r)
    out = {}
    for page, rs in by_page.items():
        by_ref = {}
        for r in rs:
            by_ref.setdefault(r["ref"], r)
        for r in rs:
            par = r.get("parent") or ""
            p = by_ref.get(par)
            if not p or p.get("x") is None or r.get("x") is None:
                continue
            if p.get("placed") != "hand" or r.get("placed") != "hand":
                continue
            if r.get("unit", 1) not in (None, 1) or p.get("unit", 1) not in (None, 1):
                continue
            key = p["ipn"]
            fam = out.setdefault(key, {"page": page, "parent": par, "kids": []})
            if fam["parent"] != par or fam["page"] != page:
                continue
            fam["kids"].append((r["ipn"], float(r["x"]) - float(p["x"]),
                                float(r["y"]) - float(p["y"]),
                                int(r.get("rot") or 0), number_of(r["ref"])))
    for fam in out.values():
        fam["kids"].sort(key=lambda k: k[4])
    return {k: [kid[:4] for kid in v["kids"]] for k, v in out.items()
            if v["kids"]}


def boxes_of(rows, blocks, model=None, templates=None, rooms_rows=None):
    """One node list, one walk. A node is a part instance or a room; both
    carry `(uuid, path)` and a parent that may be either. A room draws a
    named box around its children; a part draws its symbol. Nothing asks
    whether a child is family or a room, and a multi-unit package stays
    one node with its units side by side - kicad-update SKILL, Units."""
    # part nodes, one per reference, its unit rows together
    by_ref = {}
    for row in rows:
        by_ref.setdefault(row["ref"], []).append(row)
    key_ref = {}
    for ref, rs in by_ref.items():
        for r in rs:
            key_ref[r["id"]] = ref
    node = {}          # id -> {"kind", "name", "rows", "parent"}
    for ref, rs in by_ref.items():
        node[("p", ref)] = {"kind": "part", "name": ref, "rows": rs,
                            "parent": None}
    for r in (rooms_rows or []):
        node[("r", r["id"])] = {
            "kind": "room", "name": r["name"], "rows": [], "parent": None,
            "row": r,
            "pkey": r.get("parent") or ""}

    def id_of(pkey):
        """The node one id names: a room if one holds it, else the
        reference of the part instance it names."""
        if not pkey:
            return None
        if ("r", pkey) in node:
            return ("r", pkey)
        ref = key_ref.get(pkey)
        return ("p", ref) if ref else None

    for ref, rs in by_ref.items():
        pk = rs[0].get("parent_key") or ""
        nid = id_of(pk)
        if nid is None and rs[0].get("parent"):
            nid = ("p", rs[0]["parent"]) if ("p", rs[0]["parent"]) in node \
                else None
        node[("p", ref)]["parent"] = nid
    for nid, n in node.items():
        if n["kind"] == "room":
            n["parent"] = id_of(n.pop("pkey"))
    # a parent the record names but nothing draws is still a box
    for nid, n in list(node.items()):
        if n["parent"] and n["parent"] not in node:
            n["parent"] = None
    kids = {}
    for nid, n in node.items():
        if n["parent"]:
            kids.setdefault(n["parent"], []).append(nid)

    def order_key(nid):
        n = node[nid]
        if n["rows"]:
            return order_of(n["rows"][0])
        return (0, n["name"], "", 0, 0, 0)

    def pack(nid):
        n = node[nid]
        items = []
        for row in sorted(n["rows"], key=lambda r: r.get("unit") or 1):
            w, h = size_of(row, blocks, model)
            items.append((w, h, ("part", row)))
        for kid in sorted(kids.get(nid, []), key=order_key):
            kw, kh, kflat, krooms = pack(kid)
            items.append((kw, kh, ("box", (kflat, krooms))))
        if not items:
            return 0.0, 0.0, [], []
        placed, w, h = shelf(items, box_extent(items, down=True), down=True)
        flat, out_rooms = [], []
        for x, y, (kind, payload) in placed:
            if kind == "part":
                flat.append((x, y, payload))
            else:
                kflat, krooms = payload
                flat.extend((x + dx, y + dy, r) for dx, dy, r in kflat)
                out_rooms.extend((rx + x, ry + y, rw, rh, nd)
                                 for rx, ry, rw, rh, nd in krooms)
        if n["kind"] == "room":
            pad = 2 * GRID
            flat = [(x + pad, y + pad, r) for x, y, r in flat]
            out_rooms = [(rx + pad, ry + pad, rw, rh, nd)
                         for rx, ry, rw, rh, nd in out_rooms]
            w, h = w + 2 * pad, h + 2 * pad
            out_rooms.insert(0, (0.0, 0.0, w, h, n))
        return w, h, flat, out_rooms

    out = []
    tops = [nid for nid, n in node.items() if not n["parent"]]
    for nid in sorted(tops, key=order_key):
        w, h, flat, rms = pack(nid)
        if flat:
            out.append((w, h, flat, rms))
    return out


CORNERS = {
    # corner: which box corner the label anchors to, and how it justifies
    "nw": (0, 0, -1, "left bottom"),
    "ne": (1, 0, -1, "right bottom"),
    "sw": (0, 1, +1, "left top"),
    "se": (1, 1, +1, "right top"),
}


def room_sexp(room, x, y, w, h):
    """A room's box, and its name placed at the corner the room names.
    Both objects take their uuid from the room's own, so a rename or a
    move leaves the same object on the sheet - T2.4a."""
    x0, y0 = snap(x), snap(y)
    x1, y1 = snap(x + w), snap(y + h)
    name = room["name"]
    row = room.get("row") or {}
    ox, oy, dy, just = CORNERS.get((row.get("corner") or "nw").lower(),
                                   CORNERS["nw"])
    tx = (x1 - GRID / 2) if ox else (x0 + GRID / 2)
    ty = (y1 if oy else y0) + dy * GRID / 2
    ru = row.get("uuid") or uid("roombox", name, f"{x0:.2f}", f"{y0:.2f}")
    return (
        "\t(rectangle\n"
        f"\t\t(start {x0:.2f} {y0:.2f})\n"
        f"\t\t(end {x1:.2f} {y1:.2f})\n"
        "\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type dash)\n\t\t)\n"
        "\t\t(fill\n\t\t\t(type none)\n\t\t)\n"
        f"\t\t(uuid \"{uid('roombox', ru)}\")\n"
        "\t)\n"
        f"\t(text \"{esc(name)}\"\n"
        f"\t\t(at {tx:.2f} {ty:.2f} 0)\n"
        f"\t\t(effects\n\t\t\t(font\n\t\t\t\t(size {FONT} {FONT})\n\t\t\t)\n"
        f"\t\t\t(justify {just})\n\t\t)\n"
        f"\t\t(uuid \"{uid('roomname', ru)}\")\n"
        "\t)\n"
    )


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


def draw_one(row, x, y, rot, blocks, project, ctx):
    """One drawing at a place on the page: a sheet symbol with its
    fittings, or a symbol with its labels."""
    model = ctx["model"]
    if row.get("sheet"):
        pins = model.sheet_pins(row["sheet"])
        sx, sy = snap(x), snap(y)
        body = sheet_sexp(
            project, ctx["path_of"](row["path"]), row["ref"],
            f"{project}-{slug(row['sheet'])}.kicad_sch",
            ctx["numbers"].get(row["id"], 0),
            sx, sy, SHEET_W, pins, row["uuid"])
        named = model.pins.get(row["id"]) or {}
        body += pin_fittings_sexp(
            row["uuid"], sx, sy, SHEET_W, pins, named,
            {n: model.kind(net, ctx["page"]) for n, net in named.items()})
        return body
    half_w, half_h, bottom, right = geom(row, blocks, model)
    body = instance_sexp(project, ctx["path_of"], row, x, y, bottom, right,
                         rot)
    body += labels_sexp(row, blocks[row["symbol"]], x, y, rot, model)
    return body


def flow(rows, blocks, project, ctx, width, start_y, height=None):
    """Lay the sheet. A drawing with a place in the record is drawn there,
    as it was pulled or arranged by hand. The rest are laid as boxes, not
    as text: a box is a parent and everything under it, packed roughly
    square and sized by its contents, from a template when its family has
    one. Boxes pack the page below what has a place, largest first, and a
    box never splits across a wrap. `ctx`: page, model, path_of, numbers,
    templates."""
    model = ctx["model"]
    body, page_h, page_w = "", start_y, MARGIN + PORT_W
    placed_rows = [r for r in rows if r.get("x") is not None
                   and r.get("y") is not None and r.get("placed") == "hand"]
    free = [r for r in rows if r not in placed_rows]
    packed_out = ctx.setdefault("packed", [])
    low = start_y
    for row in placed_rows:
        x, y, rot = float(row["x"]), float(row["y"]), int(row.get("rot") or 0)
        half_w, half_h, _, _ = geom(row, blocks, model)
        body += draw_one(row, x, y, rot, blocks, project, ctx)
        if row.get("sheet"):
            page_h = max(page_h, y + 2 * half_h + GRID + MARGIN)
            page_w = max(page_w, x + 2 * half_w + GRID + MARGIN + PORT_W)
            low = max(low, y + 2 * half_h)
        else:
            page_h = max(page_h, y + half_h + GRID + MARGIN)
            page_w = max(page_w, x + half_w + GRID + MARGIN + PORT_W)
            low = max(low, y + half_h)
    if placed_rows:
        start_y = snap(low + 6 * GRID)
        page_h = max(page_h, start_y)
    if not free:
        return body, page_h, page_w

    boxes = boxes_of(free, blocks, model, ctx.get("templates"),
                     ctx.get("rooms"))
    boxes.sort(key=lambda b: -(b[0] * b[1]))
    items = [(w, h, (flat, rooms)) for w, h, flat, rooms in boxes]
    room = (height or width) - start_y - MARGIN
    placed, _, used_h = shelf(items, max(room, GRID), gap=BOX_GAP, down=True)

    for bx, by, (flat, rooms) in placed:
        drawn, refs = [], set()
        for rx, ry, rw, rh, room in rooms:
            body += room_sexp(room, MARGIN + bx + rx,
                              start_y + by + ry + GRID, rw, rh)
        for dx, dy, row in flat:
            half_w, half_h, bottom, right = geom(row, blocks, model)
            x = snap(MARGIN + bx + dx + half_w)
            y = snap(start_y + by + dy + half_h + GRID)
            page_h = max(page_h, y + half_h + GRID + MARGIN)
            # the port area sits at the right edge; a page is sized to
            # hold it beside the drawing
            page_w = max(page_w, x + half_w + GRID + MARGIN + PORT_W)
            rot = int(row.get("rot") or 0)
            if row.get("sheet"):
                body += draw_one(row, x - half_w, y - half_h, 0, blocks,
                                 project, ctx)
                packed_out.append((row["uuid"], snap(x - half_w),
                                   snap(y - half_h), 0))
            else:
                body += draw_one(row, x, y, rot, blocks, project, ctx)
                packed_out.append((row["uuid"], x, y, rot))
            drawn.append((x, y, half_w, half_h))
            refs.add(row["ref"])
        if len(refs) > 1:
            body += outline_sexp(drawn)
    return body, page_h, page_w


def fit_paper(lay, fixed):
    """Lay the page out on each sheet size in turn and take the first it
    fits. A fixed size that is too small falls through to the ANSI run."""
    sizes = ([fixed] + [p for p in PAPER_ORDER if p != fixed]) if fixed \
        else PAPER_ORDER
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


def write_page(board, project, page, rows, blocks, fixed, ctx):
    """One page file: every drawing the record puts on it. `rows` are the
    drawings, each with its paths."""
    name = f"{project}-{slug(page)}.kicad_sch"
    path = Path(board) / name
    needed = {r["symbol"] for r in rows if not r["sheet"]}
    model = ctx["model"]

    if path.exists():
        src = path.read_text()
        fresh = [r for r in rows if r["uuid"] not in existing_uuids(src)]
        if fresh:
            paper = paper_of(src)
            width, height = PAPERS.get(paper, PAPERS["A"])
            body, tall, wide = flow(fresh, blocks, project, ctx, width,
                                    snap(lowest_used(src) + 10 * GRID),
                                    height)
            # what was there stays put; the paper grows to hold what is
            # appended below it, ANSI sizes up to E
            for size in PAPER_ORDER:
                w, h = PAPERS[size]
                if tall <= h and wide <= w and (w, h) >= (width, height):
                    if size != paper:
                        src = re.sub(r'\(paper "[^"]*"\)', f'(paper "{size}")',
                                     src, count=1)
                        print(f"    {page}: paper {paper} to {size}")
                    break
            src = merge_sheet(src, blocks, needed, body)
        src, buses = port_area(src, page, model)
        if buses:
            close = src.rstrip().rfind(")")
            src = src[:close] + buses + src[close:]
        path.write_text(src)
        return name, len(fresh), len(rows) - len(fresh)

    lib_symbols = "".join(indent_block(blocks[k], 1).rstrip() + "\n"
                          for k in sorted(needed))

    def lay(width, height=None):
        return flow(rows, blocks, project, ctx, width, TOP, height)

    paper, body = fit_paper(lay, fixed)
    src = new_sheet(project, uid(project, "file", page), paper, lib_symbols,
                    body)
    src, buses = port_area(src, page, model)
    if buses:
        close = src.rstrip().rfind(")")
        src = src[:close] + buses + src[close:]
    path.write_text(src)
    return name, len(rows), 0


def write_root(board, project, root, pages, model, fixed=None):
    """The root is the tool's. It carries one sheet symbol per root page,
    each pin a net or bus that leaves the page, each pin joined to a root
    label by a stub. Rewritten on every run; a page keeps its sheet uuid
    and its page number. Returns {page: (sheet uuid, page number)}."""
    path = Path(board) / f"{project}.kicad_sch"
    src = path.read_text() if path.exists() else None
    have = root_sheets(src)
    used = {n for _, n in have.values()}
    numbers, nxt = {}, 2
    for page in pages:
        if page in have and have[page][1]:
            numbers[page] = have[page][1]
    for page in pages:
        if page not in numbers:
            while nxt in used:
                nxt += 1
            numbers[page] = nxt
            used.add(nxt)
    ids = {page: (have[page][0] if page in have
                  else uid(project, "sheet", page)) for page in pages}

    def lay(width, height=None):
        body, x, y = "", MARGIN, TOP
        col_w = SHEET_W + STUB + 16 * GRID
        tall, wide = y, x
        limit = (height or width) - MARGIN
        for page in pages:
            pins = model.sheet_pins(page)
            h = sheet_height(len(pins))
            if y + h > limit and y > TOP:
                x, y = snap(x + col_w), TOP
            body += sheet_sexp(project, f"/{root}", page,
                               f"{project}-{slug(page)}.kicad_sch",
                               numbers[page], x, y, SHEET_W, pins, ids[page])
            body += pin_fittings_sexp(ids[page], x, y, SHEET_W, pins,
                                      {p: p for p in pins},
                                      {p: "label" for p in pins})
            tall = max(tall, y + h + 2 * GRID + MARGIN)
            wide = max(wide, x + col_w + MARGIN)
            y = snap(y + h + 4 * GRID)
        return body, tall, wide

    paper, body = fit_paper(lay, fixed or (paper_of(src) if src else None))
    if paper not in PAPERS:
        paper = "A"
    instances = "".join(
        f"\t\t(path \"/{root}/{ids[page]}\"\n\t\t\t(page \"{numbers[page]}\")\n\t\t)\n"
        for page in pages)
    path.write_text(
        "(kicad_sch\n"
        f"\t(version {SCH_VERSION})\n"
        "\t(generator \"kicad-update.py\")\n"
        "\t(generator_version \"10.0\")\n"
        f"\t(uuid \"{root}\")\n"
        f"\t(paper \"{paper}\")\n"
        "\t(lib_symbols\n\t)\n"
        f"{body}"
        "\t(sheet_instances\n\t\t(path \"/\"\n\t\t\t(page \"1\")\n\t\t)\n"
        f"{instances}\t)\n"
        "\t(embedded_fonts no)\n"
        ")\n")
    return {page: (ids[page], numbers[page]) for page in pages}


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

    def normalize(path):
        """n8.2 - the sheet must load with no dialogs. KiCad's own writer
        has the final word on the format."""
        run = subprocess.run(["kicad-cli", "sch", "upgrade", "--force",
                              str(path)], capture_output=True, text=True)
        if run.returncode != 0:
            raise Bad(f"kicad-cli could not normalize {path}: "
                      + (run.stderr or run.stdout).strip())

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
                rows = instances(con)
                model = Nets(con, rows)
                root_pages = sorted({r["page"] for r in rows if r["page"]
                                     and r["page"] not in model.sheet_pages})
                write_root(board, project, root, root_pages, model)
                normalize(root_path)
                root_src = root_path.read_text()
                print(f"{project}.kicad_sch  rewritten, {len(root_pages)} "
                      "page(s)")
                sheets = push_instances(con, board, project, root, root_src,
                                        rows)
                print(f"push  {sum(sheets.values())} instance(s) updated on "
                      f"{len(sheets)} sheet(s)")
                labels = push_labels(con, board, project, root, root_src,
                                     rows, model)
                print(f"push  {sum(labels.values())} label(s) written on "
                      f"{len(labels)} sheet(s)")
                # the simulation, T2.6d and T2.6e: the models file, the
                # Sim.* fields, the sources and the directive
                sim = active_sim(con)
                models = write_models(con, board, project)
                if models:
                    print(f"models/{project}.sp  {len(models)} op-amp "
                          "model(s): " + " ".join(models[k][0] for k in models))
                ensure_sim_symbols(board, project)
                fields, skipped = push_sim_fields(con, board, project,
                                                  root_src, rows, sim, models)
                fittings = push_sim_fittings(con, board, project, root,
                                             root_src, rows, sim)
                if sim:
                    print(f"sim   {sim['name']} {sim['kind']}: "
                          f"{sum(fields.values())} symbol(s) fielded on "
                          f"{len(fields)} sheet(s), "
                          f"{sum(fittings.values())} source(s) drawn")
                else:
                    print(f"sim   none: {sum(fields.values())} symbol(s) "
                          "cleared")
                for line in skipped:
                    print(f"    not simulated  {line}")
                fixed, unknown = push_board(board, project, root, root_src,
                                            rows)
                print(f"push  {fixed} footprint path(s) rewritten on the "
                      "board" + (f"; not in the record: {' '.join(unknown)}"
                                 if unknown else ""))
                # last: normalizing a sheet after this drops the aliases
                wrote = write_bus_aliases(board, project, model)
                print(f"push  bus aliases written into {wrote} sheet(s)")
            else:
                applied, reported = pull_fields(con, library, project)
                print(f"pull  {len(applied)} field(s) into the record")
                written, moved = pull_positions(con, board, project, root_src)
                print(f"pull  {written} place(s) into the record, "
                      f"{moved} moved since last pulled")
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

    # The return direction. Read every page back before anything is placed,
    # and enter what the User put there.
    con = connect(board)
    try:
        by_file = page_files(con, project, root_src)
        placed, placed_sheets = read_pages(board, project, by_file)
        rows = instances(con)
        known = {r["uuid"] for r in rows}
        parts = {r[0] for r in con.execute("select ipn from parts_table")}
        refs = {r[0]: r[1] for r in con.execute(
            "select ref, id from ref_table where ref is not null")}
        sub_paths = {}   # sub-sheet page -> its instance paths
        for r in rows:
            if r["sheet"]:
                sub_paths.setdefault(r["sheet"], []).append(
                    f"{r['path']}/{r['uuid']}" if r["path"] else r["uuid"])
        entered, unresolved, conflicts, fixes = [], [], [], {}
        for u, (fname, page, sym) in sorted(placed.items()):
            if u in known or is_sim_fitting(project, sym):
                continue
            ipn = assign.get(u) or sym["props"].get("ipn", "").strip()
            if not ipn or ipn not in parts:
                unresolved.append((u, fname, sym))
                continue
            ref = sym["props"].get("Reference", "").strip()
            if ref in refs and refs[ref] != u:
                conflicts.append((u, fname, ref, refs[ref]))
                continue
            paths = sub_paths.get(page, [""])
            if page in sub_paths and not paths:
                unresolved.append((u, fname, sym))
                continue
            fix = {}
            for i, path in enumerate(paths):
                use = ref if i == 0 else ""
                if not use or use.endswith("?") or (use in refs):
                    prefix = re.match(r"^[A-Za-z]+", ref or "U")
                    use = next_ref_free(con, prefix.group(0) if prefix
                                        else "U", set(refs))
                con.execute("insert into ref_table (id, ipn, parent, ref, "
                            "page, unit, path) values (?, ?, null, ?, ?, "
                            "null, ?)", (u, ipn, use, page, path))
                refs[use] = u
                if i == 0:
                    ref = use
            entered.append((u, fname, ref, ipn))
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
    # on a sheet. Same ref, unit numbering from 1, one row per path.
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
            con.execute("update ref_table set unit = 1 where ipn = ? "
                        "and unit is null", (ipn,))
            for i1, ref, page, parent, sym in con.execute(
                    "select id, ref, page, parent, sym_uuid from ref_table "
                    "where ipn = ? and unit = 1 and kind = 'part'",
                    (ipn,)).fetchall():
                for unit in range(2, units + 1):
                    have = con.execute(
                        "select 1 from ref_table where ref = ? and unit = ? "
                        "and kind = 'part'", (ref, unit)).fetchone()
                    if have:
                        continue
                    con.execute(
                        "insert into ref_table (id, sym_uuid, kind, ipn, "
                        "parent, ref, page, unit) "
                        "values (?,?,'part',?,?,?,?,?)",
                        (str(uuid.uuid4()), sym, ipn, parent, ref, page,
                         unit))
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

    con = connect(board)
    try:
        model = Nets(con, rows)
    finally:
        con.close()
    unplaced = [r for r in rows if not r["page"]]
    nosymbol = [r for r in rows if r["page"] and not r["symbol"]]
    drawable = [r for r in drawings(rows) if r["page"] and r["symbol"]
                and r["uuid"] not in elsewhere]
    orphan = [r for r in drawable if r["page"] in model.sheet_pages
              and not any(r["path"] for r in [r])]
    # a drawing in a sub-sheet with no instance path has no sheet to be in
    drawable = [r for r in drawable if r not in orphan]
    if not drawable and not placed:
        raise Bad("nothing to place. Every instance is missing a page, a "
                  "symbol, or both")

    blocks = library_blocks(board, project,
                            {r["symbol"] for r in drawable if not r["sheet"]})
    root_pages = sorted({r["page"] for r in drawable
                         if r["page"] not in model.sheet_pages})
    sub_pages = sorted({r["page"] for r in drawable
                        if r["page"] in model.sheet_pages})

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

    made = write_project_file(board, project)
    sheets = write_root(board, project, root, root_pages, model)
    normalize(root_path)
    print(f"{project}.kicad_pro  {'written' if made else 'kept'}")
    print(f"{project}.kicad_sch  rewritten, {len(root_pages)} page(s)")

    # page numbers: root pages as the root says, sub-sheet instances after
    starts = root_page_of(rows)
    numbers, nxt = {}, max([n for _, n in sheets.values()] + [1]) + 1
    for r in sorted((r for r in rows if r["sheet"]),
                    key=lambda r: (r["page"], r["path"], r["ref"])):
        numbers[r["id"]] = nxt
        nxt += 1

    templates = family_templates(rows)
    touched = set(refreshed)
    for page in root_pages + sub_pages:
        on_page = [r for r in drawable if r["page"] == page]
        ctx = {"page": page, "model": model, "numbers": numbers,
               "templates": templates,
               "rooms": ROOM_ROWS,
               "path_of": lambda p, page=page: kicad_path(root, sheets,
                                                          starts, page, p)}
        name, new, kept = write_page(board, project, page, on_page, blocks,
                                     None, ctx)
        if ctx.get("packed"):
            con = connect(board)
            try:
                for u, px, py, prot in ctx["packed"]:
                    con.execute("update ref_table set x = ?, y = ?, rot = ?, "
                                "placed = 'tool' where uuid = ?",
                                (px, py, prot, u))
                con.commit()
            finally:
                con.close()
        touched.discard(name)
        normalize(board / name)
        came_in = sum(1 for _, f, _, _ in entered if f == name)
        print(f"    {name}  {new} placed, {kept} left as they were, "
              f"{came_in} entered")
    for name in sorted(touched):
        normalize(board / name)

    # last: normalizing a sheet after this drops the aliases
    wrote = write_bus_aliases(board, project, model)
    print(f"    bus aliases written into {wrote} sheet(s)")

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
    if orphan:
        print(f"\n{len(orphan)} drawing(s) in a sub-sheet with no instance, "
              "not drawn: " + " ".join(sorted(r["ref"] for r in orphan)))
    if nosymbol:
        print(f"\n{len(nosymbol)} instance(s) with a page and no symbol: "
              + " ".join(sorted(r["ref"] for r in nosymbol)))
    if unplaced:
        print(f"{len(unplaced)} instance(s) with no page, not drawn: "
              + " ".join(sorted(r["ref"] for r in unplaced)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
