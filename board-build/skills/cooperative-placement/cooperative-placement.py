#!/usr/bin/env python3
"""cooperative-placement — gather one block's footprints for the User to place.

    cooperative-placement.py <board-dir> <room|ref> [--except REF ...] [--gap MM] [--width MM]

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
board stay open. No commit is opened: KiCad 10.0.5 routes `BeginCommit`
to the schematic editor's handler when both editors are open, and it
crashes there.

Pack: footprints by bounding box without text, tallest first, into rows no
wider than `--width`, `--gap` apart on both axes. The cluster's lower right
corner sits `--gap` above the upper right corner of Edge.Cuts. Nothing is
saved; the User saves.
"""

import argparse
import os
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


def main():
    ensure_kipy()
    from kipy import KiCad
    from kipy.geometry import Vector2
    from kipy.proto.board.board_types_pb2 import BoardLayer

    ap = argparse.ArgumentParser()
    ap.add_argument("board_dir")
    ap.add_argument("key")
    ap.add_argument("--except", dest="skip", nargs="+", default=[])
    ap.add_argument("--gap", type=float, default=0.5)
    ap.add_argument("--width", type=float, default=16.0)
    a = ap.parse_args()

    db = Path(a.board_dir) / "board.db"
    if not db.exists():
        raise Bad(f"no record at {db}")
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
