#!/usr/bin/env python3
"""block-outline — box each named group of parts on the open board.

    block-outline.py <board-dir> <room|ref> [<room|ref> ...]

Each name is a room by `ref_table.name`, or a part by `ref`, on the open
board, and everything under it by `parent`. Rooms of the same name nested
in the one named are the same group. The script finds the group's
footprints on the board open in KiCad and draws one rectangle on
User.Comments around them, 0.5 mm outside, with the name at its top left.

A box drawn before for a name is replaced: the label on User.Comments
whose text is the name, and the rectangle whose corner sits under it.
One read of the board, one remove, one create. Nothing is moved. Nothing
is saved; the User saves.
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
MARGIN = 500_000      # nm, box outside the parts
LABEL_UP = 300_000    # nm, label baseline above the box


class Bad(SystemExit):
    def __init__(self, message):
        super().__init__(f"block-outline: {message}")


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


def group(con, key, board):
    """The refs of every part under the outermost node named `key` on
    `board`."""
    rows = [r[0] for r in con.execute(
        "select id from ref_table where (name = ? or ref = ?) "
        "and (? is null or board = ?)", (key, key, board, board))]
    if not rows:
        raise Bad(f"no room or ref named {key!r} on board {board!r}")
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
        raise Bad(f"{key!r} names unrelated nodes on board {board!r}; "
                  f"name a part instead")
    refs = set()
    for t in tops:
        refs.update(r[0] for r in con.execute("""
            with recursive t(id) as (select ? union all
                select r.id from ref_table r join t on r.parent = t.id)
            select r.ref from t join ref_table r using (id)
            where r.kind = 'part' and r.ref is not null""", (t,)))
    return refs


def main():
    ensure_kipy()
    from kipy import KiCad
    from kipy.board_types import BoardRectangle, BoardText
    from kipy.geometry import Vector2
    from kipy.proto.board.board_types_pb2 import BoardLayer
    from kipy.proto.common.types.enums_pb2 import HA_LEFT, VA_BOTTOM

    ap = argparse.ArgumentParser()
    ap.add_argument("board_dir")
    ap.add_argument("names", nargs="+")
    a = ap.parse_args()

    db = Path(a.board_dir) / "board.db"
    if not db.exists():
        raise Bad(f"no record at {db}")
    try:
        b = KiCad(timeout_ms=10000).get_board()
    except Exception as e:
        raise Bad(f"no board open in KiCad: {e}")
    stem = Path(b.name).stem
    board = stem.split("-board-", 1)[1] if "-board-" in stem else None

    con = sqlite3.connect(db)
    groups = [(n, group(con, n, board)) for n in a.names]

    fps = list(b.get_footprints())
    bbs = b.get_item_bounding_box(fps, include_text=False)
    box = {f.reference_field.text.value: bb for f, bb in zip(fps, bbs) if bb is not None}

    names = {n for n, _ in groups}
    old = [t for t in b.get_text() if isinstance(t, BoardText)
           and t.layer == BoardLayer.BL_Cmts_User and t.value in names]
    corners = {(t.position.x, t.position.y + LABEL_UP) for t in old}
    old += [r for r in b.get_shapes() if isinstance(r, BoardRectangle)
            and r.layer == BoardLayer.BL_Cmts_User
            and (r.top_left.x, r.top_left.y) in corners]
    if old:
        b.remove_items(old)

    new, empty = [], []
    for name, refs in groups:
        on = [box[r] for r in refs if r in box]
        if not on:
            empty.append(name)
            continue
        x0 = min(q.pos.x for q in on) - MARGIN
        y0 = min(q.pos.y for q in on) - MARGIN
        x1 = max(q.pos.x + q.size.x for q in on) + MARGIN
        y1 = max(q.pos.y + q.size.y for q in on) + MARGIN
        r = BoardRectangle()
        r.layer = BoardLayer.BL_Cmts_User
        r.top_left = Vector2.from_xy(x0, y0)
        r.bottom_right = Vector2.from_xy(x1, y1)
        r.attributes.stroke.width = 100_000
        t = BoardText()
        t.layer = BoardLayer.BL_Cmts_User
        t.value = name
        t.position = Vector2.from_xy(x0, y0 - LABEL_UP)
        t.attributes.size = Vector2.from_xy(NM, NM)
        t.attributes.stroke_width = 150_000
        t.attributes.horizontal_alignment = HA_LEFT
        t.attributes.vertical_alignment = VA_BOTTOM
        new += [r, t]
    if new:
        b.create_items(new)

    line = f"{len(new) // 2} boxes drawn on User.Comments"
    if old:
        line += f", {len(old)} old items replaced"
    if empty:
        line += "; no footprints on the board: " + ", ".join(empty)
    print(line)


if __name__ == "__main__":
    main()
