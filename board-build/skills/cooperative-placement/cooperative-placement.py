#!/usr/bin/env python3
"""cooperative-placement — gather one block's footprints for the User to place.

    cooperative-placement.py <board-dir> <room|ref> [--except REF ...] [--gap MM] [--width MM]
    cooperative-placement.py <board-dir> --flow <strategy.json> [--dry-run]

The User places parts on the board one block at a time. This script is
Claude's half: it names the block from the record, finds its footprints in
the PCB editor that is open, packs them into a tight cluster above the
board's upper right corner, and selects them. The User drags them into
place from there.

The block is a room by `ref_table.name`, or a part by `ref`, and
everything under it by `parent` — T2.4. Rooms of the same name nested in
the one named are the same block. Two unrelated rooms of one name are
refused; name a part instead. `--except` leaves the refs it names where
they are.

The board is live: KiCad's IPC API, through `kicad-python` in the tool's
own venv, `.venv` at the plugin root, made on first run. KiCad and the
board stay open. No commit is opened. KiCad 10.0.5 keeps the schematic
editor's API handler after that editor is closed, and a board call that
reaches it crashes KiCad; so the Schematic Editor is not closed while
KiCad runs.

Pack: footprints by bounding box without text, tallest first, into rows no
wider than `--width`, `--gap` apart on both axes. The cluster's lower right
corner sits `--gap` above the upper right corner of Edge.Cuts. Nothing is
saved; the User saves.

Flow, `--flow`: places a scope's parts on the board, block by block along
a flow, from a strategy the LLM writes — SKILL §6. The strategy holds the
judgment: scope, anchor, direction, blocks in flow order. The script holds
the geometry:

- Each block is laid out in its own frame. Its parent goes at the origin,
  turned so its pads on the nets of earlier blocks face back along the
  flow and its pads on the nets of later blocks face forward.
- The block's other parts follow one at a time, most connected to what is
  laid first. Each is turned 0, 90, 180 or 270 and set where its pads are
  nearest the pads they share a net with, `gap` clear of what is laid.
  A net on more than half the scope's parts, ground or a rail, pulls
  nothing.
- The blocks are set along the flow from the anchor, `block_gap` apart,
  each pushed forward until clear of every footprint outside the scope.
- One read of the board, one `update_items`, one read back. The read back
  checks every part outside the scope is where it was, and every pad
  inside it is where the plan put it. Front-side footprints only.
"""

import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
PY = VENV / "bin" / "python"
NM = 1_000_000


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"cooperative-placement: {message}")


def ensure_kipy():
    """Run under the tool's venv, with kicad-python in it."""
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not PY.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True,
                       capture_output=True)
    probe = subprocess.run([str(PY), "-c", "import kipy"], capture_output=True)
    if probe.returncode != 0:
        run = subprocess.run([str(VENV / "bin" / "pip"), "install", "-q",
                              "kicad-python"], capture_output=True, text=True)
        if run.returncode != 0:
            raise Bad("could not install kicad-python: "
                      + (run.stderr or run.stdout).strip()[-200:])
    os.execv(str(PY), [str(PY), __file__, *sys.argv[1:]])


def block(db, key):
    """The refs of every part in the block `key` names, by board."""
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    rows = con.execute("select id, parent from ref_table where name = ? or ref = ?",
                       (key, key)).fetchall()
    if not rows:
        raise Bad(f"no room or ref named {key!r} in {db}")
    ids = {r[0] for r in rows}
    parent = dict(con.execute("select id, parent from ref_table"))

    def nested(i):
        p = parent.get(i)
        while p:
            if p in ids:
                return True
            p = parent.get(p)
        return False

    tops = [i for i in ids if not nested(i)]
    if len(tops) > 1:
        names = con.execute(
            f"select coalesce(p.ref, '—') from ref_table r left join ref_table p on p.id = r.parent "
            f"where r.id in ({','.join('?' * len(tops))})", tops).fetchall()
        raise Bad(f"{key!r} names {len(tops)} blocks, under "
                  + ", ".join(n[0] for n in names) + "; name a part instead")
    parts = con.execute("""
        with recursive t(id) as (select ? union all
            select r.id from ref_table r join t on r.parent = t.id)
        select distinct r.ref, r.board from t join ref_table r using (id)
        where r.kind = 'part' and r.ref is not null""", tops).fetchall()
    return parts


# ---- flow placement, SKILL §6 ---------------------------------------------

DIRS = {"R": (1, 0), "L": (-1, 0), "D": (0, 1), "U": (0, -1)}
STEP = 0.25          # search grid, mm
REACH = 40.0         # search radius around the target, mm
SLACK = 2.0          # keep looking this far past the first clear spot, mm


# ---- geometry, mm, board frame ------------------------------------------

def rot(x, y, deg):
    t = math.radians(deg)
    c, s = round(math.cos(t), 12), round(math.sin(t), 12)
    return x * c + y * s, -x * s + y * c


class Part:
    """A footprint in its own frame: pads (number, x, y, net) and box."""

    def __init__(self, ref, pads, box):
        self.ref, self.pads, self.box = ref, pads, box

    def box_at(self, x, y, deg):
        x0, y0, x1, y1 = self.box
        cs = [rot(cx, cy, deg) for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        return (x + min(c[0] for c in cs), y + min(c[1] for c in cs),
                x + max(c[0] for c in cs), y + max(c[1] for c in cs))

    def pads_at(self, x, y, deg):
        out = []
        for num, px, py, net in self.pads:
            rx, ry = rot(px, py, deg)
            out.append((num, x + rx, y + ry, net))
        return out


def clear(b, boxes, gap):
    return all(b[2] + gap <= o[0] or o[2] + gap <= b[0] or
               b[3] + gap <= o[1] or o[3] + gap <= b[1] for o in boxes)


_RING = None


def ring():
    """Grid offsets within REACH, nearest first."""
    global _RING
    if _RING is None:
        n = int(REACH / STEP)
        pts = [(i * STEP, j * STEP) for i in range(-n, n + 1) for j in range(-n, n + 1)
               if (i * i + j * j) * STEP * STEP <= REACH * REACH]
        pts.sort(key=lambda p: (p[0] ** 2 + p[1] ** 2, p[1], p[0]))
        _RING = pts
    return _RING


# ---- one block ------------------------------------------------------------

def lay_block(parts, parent, ignore, gap, back, fwd, d):
    """Place `parts` around `parent` at the origin. Returns {ref: (x, y, deg)}."""
    by = {p.ref: p for p in parts}
    par = by[parent]

    def facing(deg):
        s = 0.0
        for _, x, y, net in par.pads_at(0, 0, deg):
            a = x * d[0] + y * d[1]
            if net in back:
                s -= a
            if net in fwd:
                s += a
        return s
    pdeg = max((0, 90, 180, 270), key=lambda g: (round(facing(g), 6), -g))
    laid = {parent: (0.0, 0.0, pdeg)}
    boxes = [par.box_at(0, 0, pdeg)]
    pads = [(net, x, y) for _, x, y, net in par.pads_at(0, 0, pdeg) if net and net not in ignore]

    rest = [p for p in parts if p.ref != parent]
    while rest:
        nets_laid = {n for n, _, _ in pads}

        def weight(p):
            return sum(1 for *_, net in p.pads if net in nets_laid and net not in ignore)
        c = max(rest, key=lambda p: (weight(p), -rest.index(p)))
        rest.remove(c)
        mine = [(px, py, net) for _, px, py, net in c.pads
                if net and net not in ignore and net in nets_laid]
        partners = {n: [(x, y) for m, x, y in pads if m == n] for _, _, n in mine}
        if mine:
            tx = sum(x for n in partners for x, _ in partners[n]) / sum(map(len, partners.values()))
            ty = sum(y for n in partners for _, y in partners[n]) / sum(map(len, partners.values()))
        else:
            tx = ty = 0.0

        def cost(x, y, deg):
            s = 0.0
            for px, py, net in mine:
                rx, ry = rot(px, py, deg)
                s += min(math.hypot(x + rx - qx, y + ry - qy) for qx, qy in partners[net])
            return s + 0.1 * math.hypot(x, y)

        best = None
        for deg in (0, 90, 180, 270):
            if mine:
                cx = sum(rot(px, py, deg)[0] for px, py, _ in mine) / len(mine)
                cy = sum(rot(px, py, deg)[1] for px, py, _ in mine) / len(mine)
            else:
                cx = cy = 0.0
            first = None
            for ox, oy in ring():
                r = math.hypot(ox, oy)
                if first is not None and r > first + SLACK:
                    break
                x, y = tx + ox - cx, ty + oy - cy
                if not clear(c.box_at(x, y, deg), boxes, gap):
                    continue
                first = r if first is None else first
                k = (round(cost(x, y, deg), 6), deg, r)
                if best is None or k < best[0]:
                    best = (k, x, y, deg)
        if best is None:
            raise ValueError(f"no clear spot for {c.ref} within {REACH} mm")
        _, x, y, deg = best
        laid[c.ref] = (x, y, deg)
        boxes.append(c.box_at(x, y, deg))
        pads += [(net, px, py) for _, px, py, net in c.pads_at(x, y, deg)
                 if net and net not in ignore]
    return laid


def plan(blocks, d, gap, block_gap, ignore, keepouts, start, align, anchor_nets):
    """Set laid-out blocks along the flow. `blocks`: [(parts, parent)].
    `align`: ('origin'|'min'|'max', cross value). Returns {ref: (x, y, deg)}."""
    e = (abs(d[1]), abs(d[0]))
    nets = [{net for p in parts for *_, net in p.pads if net and net not in ignore}
            for parts, _ in blocks]
    out = {}
    cursor = start
    for i, (parts, parent) in enumerate(blocks):
        back = set(anchor_nets if i == 0 else ()).union(*nets[:i]) - ignore
        fwd = set().union(*nets[i + 1:]) - ignore
        local = lay_block(parts, parent, ignore, gap, back, fwd, d)
        by = {p.ref: p for p in parts}
        lboxes = [by[r].box_at(*local[r]) for r in local]

        def along(b):
            cs = [(b[0], b[1]), (b[2], b[3])]
            v = [x * d[0] + y * d[1] for x, y in cs]
            return min(v), max(v)

        def cross(b):
            return b[0] * e[0] + b[1] * e[1], b[2] * e[0] + b[3] * e[1]
        lo = min(along(b)[0] for b in lboxes)
        mode, val = align
        if mode == "origin":
            tc = val
        elif mode == "min":
            tc = val - min(cross(b)[0] for b in lboxes)
        else:
            tc = val - max(cross(b)[1] for b in lboxes)
        ta = cursor + block_gap - lo
        for _ in range(int(400 / STEP)):
            tx, ty = d[0] * ta + e[0] * tc, d[1] * ta + e[1] * tc
            moved = [(b[0] + tx, b[1] + ty, b[2] + tx, b[3] + ty) for b in lboxes]
            if all(clear(b, keepouts, gap) for b in moved):
                break
            ta += STEP
        else:
            raise ValueError(f"block of {parent} finds no clear place along the flow")
        for r, (x, y, deg) in local.items():
            out[r] = (x + tx, y + ty, deg)
        cursor = max(along(b)[1] for b in moved)
    return out


# ---- the record -------------------------------------------------------------

def subtree(con, root):
    return [r[0] for r in con.execute("""
        with recursive t(id) as (select ? union all
            select r.id from ref_table r join t on r.parent = t.id)
        select id from t""", (root,))]


def resolve(con, key, within=None):
    """The outermost node named `key` (room name or ref), inside `within`."""
    rows = [r[0] for r in con.execute(
        "select id from ref_table where name = ? or ref = ?", (key, key))]
    if within is not None:
        rows = [r for r in rows if r in within]
    if not rows:
        raise ValueError(f"no room or ref named {key!r}" + (" in the scope" if within else ""))
    parent = dict(con.execute("select id, parent from ref_table"))
    ids = set(rows)

    def nested(i):
        p = parent.get(i)
        while p:
            if p in ids:
                return True
            p = parent.get(p)
        return False
    tops = [i for i in rows if not nested(i)]
    if len({parent.get(t) for t in tops}) > 1:
        raise ValueError(f"{key!r} names unrelated nodes; name a part instead")
    return tops


def part_refs(con, ids, board):
    q = (f"select distinct ref from ref_table where id in ({','.join('?' * len(ids))}) "
         f"and kind = 'part' and ref is not null and (board is null or ? is null or board = ?)")
    refs = [r[0] for r in con.execute(q, (*ids, board, board))]
    return sorted(refs, key=lambda r: [int(t) if t.isdigit() else t
                                       for t in re.split(r"(\d+)", r)])


# ---- the run -------------------------------------------------------------------

def flow(db, strategy_path, dry):
    from kipy import KiCad
    from kipy.geometry import Angle, Vector2
    from kipy.proto.board.board_types_pb2 import BoardLayer

    try:
        s = json.loads(strategy_path.read_text())
    except Exception as ex:
        raise Bad(f"strategy {strategy_path}: {ex}")
    for k in ("scope", "anchor", "direction", "blocks"):
        if k not in s:
            raise Bad(f"strategy has no {k!r}")
    if s["direction"] not in DIRS:
        raise Bad(f"direction {s['direction']!r} is not one of R L U D")
    d = DIRS[s["direction"]]
    gap = float(s.get("gap", 0.5))
    block_gap = float(s.get("block_gap", 2.0))
    skip = set(s.get("except", []))

    try:
        b = KiCad(timeout_ms=10000).get_board()
    except Exception as ex:
        raise Bad(f"no board open in KiCad: {ex}")
    stem = b.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    board = stem.split("-board-", 1)[1] if "-board-" in stem else None

    con = sqlite3.connect(db)
    try:
        scope_ids = set().union(*(subtree(con, t) for t in resolve(con, s["scope"])))
        scope = set(part_refs(con, list(scope_ids), board)) - skip
        blocks, seen = [], set()
        for blk in s["blocks"]:
            key = blk["block"] if isinstance(blk, dict) else blk
            ids = set().union(*(subtree(con, t) for t in resolve(con, key, scope_ids)))
            refs = [r for r in part_refs(con, list(ids), board) if r in scope]
            if not refs:
                raise ValueError(f"block {key!r} has no parts on board {board!r}")
            dup = seen.intersection(refs)
            if dup:
                raise ValueError(f"block {key!r} repeats {' '.join(sorted(dup))}")
            seen.update(refs)
            par = blk.get("parent") if isinstance(blk, dict) else None
            if par and par not in refs:
                raise ValueError(f"parent {par!r} is not in block {key!r}")
            if not par:
                par = key if key in refs else None
            if not par:
                tops = resolve(con, key, scope_ids)
                direct = sorted({r[0] for r in con.execute(
                    f"select ref from ref_table where kind = 'part' and ref is not null "
                    f"and parent in ({','.join('?' * len(tops))})", tops)
                    if r[0] in refs})
                if len(direct) != 1:
                    raise ValueError(f"block {key!r} has no single parent part; "
                                     f"name one with \"parent\"")
                par = direct[0]
            blocks.append((key, refs, par))
        left = scope - seen
        if left:
            raise ValueError("no block holds " + " ".join(sorted(left)))
    except ValueError as ex:
        raise Bad(str(ex))

    fps = list(b.get_footprints())
    byref = {f.reference_field.text.value: f for f in fps}
    missing = sorted(scope - byref.keys())
    if missing:
        raise Bad("not on the board: " + " ".join(missing))
    back = [r for r in scope if byref[r].layer != BoardLayer.BL_F_Cu]
    if back:
        raise Bad("not on the front: " + " ".join(sorted(back)))
    bbs = b.get_item_bounding_box(fps, include_text=False)
    box = {f.reference_field.text.value: bb for f, bb in zip(fps, bbs)}

    def mm(v):
        return v / NM

    parts = {}
    for r in scope:
        f = byref[r]
        ox, oy, th = mm(f.position.x), mm(f.position.y), f.orientation.degrees
        pads = []
        for p in f.definition.pads:
            px, py = rot(mm(p.position.x) - ox, mm(p.position.y) - oy, -th)
            pads.append((p.number, px, py, p.net.name))
        bb = box[r]
        cs = [rot(mm(x) - ox, mm(y) - oy, -th) for x, y in (
            (bb.pos.x, bb.pos.y), (bb.pos.x + bb.size.x, bb.pos.y),
            (bb.pos.x, bb.pos.y + bb.size.y), (bb.pos.x + bb.size.x, bb.pos.y + bb.size.y))]
        parts[r] = Part(r, pads, (min(c[0] for c in cs), min(c[1] for c in cs),
                                  max(c[0] for c in cs), max(c[1] for c in cs)))

    # A net on more than half the scope's parts, ground or a rail, says
    # nothing about where a part goes; it pulls nothing.
    on = {}
    for r, pt in parts.items():
        for *_, net in pt.pads:
            if net:
                on.setdefault(net, set()).add(r)
    ignore = {n for n, rs in on.items() if len(rs) > max(2, len(parts) / 2)}

    keep = [(mm(bb.pos.x), mm(bb.pos.y), mm(bb.pos.x + bb.size.x), mm(bb.pos.y + bb.size.y))
            for r, bb in box.items() if r not in scope and bb is not None]

    an = s["anchor"]
    e = (abs(d[1]), abs(d[0]))
    anchor_nets = set()
    if "ref" in an:
        r = an["ref"]
        if r not in byref or r in scope:
            raise Bad(f"anchor {r!r} is not a footprint outside the scope")
        bb = box[r]
        cs = [(mm(bb.pos.x), mm(bb.pos.y)), (mm(bb.pos.x + bb.size.x), mm(bb.pos.y + bb.size.y))]
        start = max(x * d[0] + y * d[1] for x, y in cs)
        fx, fy = mm(byref[r].position.x), mm(byref[r].position.y)
        align = ("origin", fx * e[0] + fy * e[1])
        anchor_nets = {p.net.name for p in byref[r].definition.pads if p.net.name}
    elif "at" in an:
        x, y = an["at"]
        start, align = x * d[0] + y * d[1], ("origin", x * e[0] + y * e[1])
    elif "edge" in an:
        edge = [sh for sh in b.get_shapes() if sh.layer == BoardLayer.BL_Edge_Cuts]
        if not edge:
            raise Bad("the board has no Edge.Cuts outline")
        eb = b.get_item_bounding_box(edge)
        x0 = min(mm(q.pos.x) for q in eb)
        y0 = min(mm(q.pos.y) for q in eb)
        x1 = max(mm(q.pos.x + q.size.x) for q in eb)
        y1 = max(mm(q.pos.y + q.size.y) for q in eb)
        side = an["edge"]
        ok = {"below": "RL", "above": "RL", "left": "UD", "right": "UD"}
        if side not in ok or s["direction"] not in ok[side]:
            raise Bad(f"edge {side!r} with direction {s['direction']!r}: below or above "
                      f"take R or L, left or right take U or D")
        start = min(x * d[0] + y * d[1] for x, y in ((x0, y0), (x1, y1)))
        align = {"below": ("min", y1 + block_gap), "above": ("max", y0 - block_gap),
                 "right": ("min", x1 + block_gap), "left": ("max", x0 - block_gap)}[side]
        keep.append((x0, y0, x1, y1))
    else:
        raise Bad("anchor takes ref, at or edge")

    try:
        out = plan([([parts[r] for r in refs], par) for _, refs, par in blocks],
                   d, gap, block_gap, ignore, keep, start, align, anchor_nets - ignore)
    except ValueError as ex:
        raise Bad(str(ex))

    rows = []
    for key, refs, par in blocks:
        for r in [par] + [r for r in refs if r != par]:
            x, y, deg = out[r]
            rows.append((key, r, x, y, deg))
    print("| # | Block | Ref | X mm | Y mm | Rot |")
    print("|---|---|---|---|---|---|")
    for i, (key, r, x, y, deg) in enumerate(rows, 1):
        print(f"| {i} | {key} | {r} | {x:.3f} | {y:.3f} | {deg} |")
    if dry:
        print(f"{s['scope']}: {len(rows)} parts planned; dry run, nothing moved")
        return

    before = {r: (f.position.x, f.position.y, round(f.orientation.degrees, 6))
              for r, f in byref.items() if r not in scope}
    moved = []
    for r in scope:
        f = byref[r]
        x, y, deg = out[r]
        f.orientation = Angle.from_degrees(deg)
        f.position = Vector2.from_xy(round(x * NM), round(y * NM))
        moved.append(f)
    b.update_items(moved)

    after = {f.reference_field.text.value: f for f in b.get_footprints()}
    shifted = [r for r, v in before.items() if r in after and
               (after[r].position.x, after[r].position.y,
                round(after[r].orientation.degrees, 6)) != v]
    off = []
    for r in scope:
        x, y, deg = out[r]
        want = {num: (px, py) for num, px, py, _ in parts[r].pads_at(x, y, deg)}
        for p in after[r].definition.pads:
            wx, wy = want[p.number]
            if abs(mm(p.position.x) - wx) > 0.002 or abs(mm(p.position.y) - wy) > 0.002:
                off.append(r)
                break
    b.clear_selection()
    b.add_to_selection([after[r] for r in scope])
    line = f"{s['scope']}: {len(scope)} parts placed along the flow and selected"
    if shifted:
        line += "; MOVED OUTSIDE THE SCOPE: " + " ".join(sorted(shifted))
    if off:
        line += "; pads not where planned: " + " ".join(sorted(off))
    print(line)


def main():
    ensure_kipy()
    ap = argparse.ArgumentParser()
    ap.add_argument("board_dir")
    ap.add_argument("key", nargs="?")
    ap.add_argument("--except", dest="skip", nargs="+", default=[])
    ap.add_argument("--gap", type=float, default=0.5)
    ap.add_argument("--width", type=float, default=16.0)
    ap.add_argument("--flow")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    db = Path(a.board_dir) / "board.db"
    if not db.exists():
        raise Bad(f"no record at {db}")
    if a.flow:
        flow(db, Path(a.flow), a.dry_run)
        return
    if not a.key:
        raise Bad("name a block, or give --flow")
    stage(db, a)


def stage(db, a):
    from kipy import KiCad
    from kipy.geometry import Vector2
    from kipy.proto.board.board_types_pb2 import BoardLayer

    parts = block(db, a.key)

    try:
        b = KiCad(timeout_ms=10000).get_board()
    except Exception as e:
        raise Bad(f"no board open in KiCad: {e}")
    stem = Path(b.name).stem
    board = stem.split("-board-", 1)[1] if "-board-" in stem else None
    refs = {r for r, bd in parts if board is None or bd is None or bd == board}
    refs -= set(a.skip)
    if not refs:
        raise Bad(f"{a.key!r} has no parts on board {board!r}")

    fps = [f for f in b.get_footprints() if f.reference_field.text.value in refs]
    missing = sorted(refs - {f.reference_field.text.value for f in fps})

    edge = [s for s in b.get_shapes() if s.layer == BoardLayer.BL_Edge_Cuts]
    if not edge:
        raise Bad("the board has no Edge.Cuts outline")
    boxes = b.get_item_bounding_box(edge)
    right = max(x.pos.x + x.size.x for x in boxes)
    top = min(x.pos.y for x in boxes)

    gap, maxw = int(a.gap * NM), int(a.width * NM)
    boxed = [(f, b.get_item_bounding_box(f, include_text=False)) for f in fps]
    boxed.sort(key=lambda t: -t[1].size.y)
    x = y = rowh = 0
    laid = []
    for f, bb in boxed:
        if x and x + bb.size.x > maxw:
            y, x, rowh = y + rowh + gap, 0, 0
        laid.append((f, bb, x, y))
        x += bb.size.x + gap
        rowh = max(rowh, bb.size.y)
    ox = right - max(px + bb.size.x for _, bb, px, _ in laid)
    oy = top - gap - (y + rowh)
    for f, bb, px, py in laid:
        f.position = Vector2.from_xy(ox + px + f.position.x - bb.pos.x,
                                     oy + py + f.position.y - bb.pos.y)
    b.update_items([f for f, *_ in laid])

    placed = [f for f in b.get_footprints() if f.reference_field.text.value in refs]
    b.clear_selection()
    b.add_to_selection(placed)
    line = f"{a.key}: {len(placed)} parts gathered and selected"
    if missing:
        line += "; not on the board: " + " ".join(missing)
    print(line)


if __name__ == "__main__":
    main()
