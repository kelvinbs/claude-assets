#!/usr/bin/env python3
"""block-outline — box each named group of parts on the open board.

    block-outline.py <board-dir> [<room|ref> ...]

Each name is a room by `ref_table.name`, or a part by `ref`, on the open
board, and everything under it by `parent`. Rooms of the same name nested
in the one named are the same group. The script finds the group's
footprints on the board open in KiCad and draws one rectangle on
User.Comments around them, 0.5 mm outside, with the name at its top left.

A box drawn before for a name is replaced: the label on User.Comments
whose text is the name, and the rectangle whose corner sits under it.
One read of the board, one remove, one create. Nothing is moved. Nothing
is saved; the User saves.

The table of contents: one text on User.Comments left of the board
outline, `Contents` and under it every box label on the layer, sorted.
Generated on every run, with no names given as well.
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv"
PY = VENV / "bin" / "python"
NM = 1_000_000
MARGIN = 500_000      # nm, box outside the parts
LABEL_UP = 300_000    # nm, label baseline above the box
KEY = "Contents"      # the table of contents' first line, and how it is found
KEY_GAP = 40 * NM     # nm, its left edge left of the board outline


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


KICAD = 'first process whose bundle identifier is "org.kicad.kicad"'
SCH = 'first window whose name contains "Schematic Editor"'


def osa(*lines):
    """Run AppleScript lines through System Events on KiCad's process."""
    args = []
    for line in ('tell application "System Events" to tell ' + KICAD,
                 *lines, 'end tell'):
        args += ["-e", line]
    run = subprocess.run(["osascript", *args], capture_output=True, text=True)
    return run.returncode, run.stdout.strip()


def sch_open():
    """The Schematic Editor's window is on screen. Its lock file is not
    proof: a crash leaves the lock behind."""
    code, out = osa(f'  return exists ({SCH})')
    if code != 0:
        raise Bad("cannot read KiCad's windows through System Events")
    return out == "true"


def close_schematic():
    """Save and close the Schematic Editor window, macOS, through System
    Events: raise it, Cmd+S only once it is the front window, then its own
    close button, never Cmd+W. Waits up to 15 s for the window to go."""
    osa('  set frontmost to true',
        f'  perform action "AXRaise" of ({SCH})',
        '  delay 0.5',
        '  if name of front window contains "Schematic Editor" then',
        '    keystroke "s" using command down',
        '    delay 2',
        f'    click (first button of ({SCH}) whose subrole is "AXCloseButton")',
        '  end if')
    for _ in range(30):
        if not sch_open():
            return
        time.sleep(0.5)


def main():
    ensure_kipy()
    from kipy import KiCad
    from kipy.board_types import BoardRectangle, BoardText
    from kipy.geometry import Vector2
    from kipy.proto.board.board_types_pb2 import BoardLayer
    from kipy.proto.common.types.enums_pb2 import HA_LEFT, VA_BOTTOM, VA_TOP

    ap = argparse.ArgumentParser()
    ap.add_argument("board_dir")
    ap.add_argument("names", nargs="*")
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
    # KiCad 10 segfaults in the Schematic Editor's API handler,
    # checkForBusy(), on a board delete while the Schematic Editor is open
    # (radar_2 crash-incidents 1.3, 1.4). Its window says it is open.
    if sch_open():
        close_schematic()
        if sch_open():
            raise Bad("the Schematic Editor is still open after save and "
                      "close: a board edit with it open crashes KiCad")
        print("Schematic Editor saved and closed")

    con = sqlite3.connect(db)
    groups = [(n, group(con, n, board)) for n in a.names]

    fps = list(b.get_footprints())
    bbs = b.get_item_bounding_box(fps, include_text=False)
    box = {f.reference_field.text.value: bb for f, bb in zip(fps, bbs) if bb is not None}

    names = {n for n, _ in groups}
    texts = [t for t in b.get_text() if isinstance(t, BoardText)
             and t.layer == BoardLayer.BL_Cmts_User]
    keys = [t for t in texts if t.value.split("\n", 1)[0] == KEY]
    labels = [t for t in texts if t not in keys]
    old = [t for t in labels if t.value in names]
    corners = {(t.position.x, t.position.y + LABEL_UP) for t in old}
    shapes = list(b.get_shapes())
    old += [r for r in shapes if isinstance(r, BoardRectangle)
            and r.layer == BoardLayer.BL_Cmts_User
            and (r.top_left.x, r.top_left.y) in corners]
    edge = [q for q in b.get_item_bounding_box(
        [s for s in shapes if s.layer == BoardLayer.BL_Edge_Cuts])
        if q is not None]
    if not edge:
        raise Bad("the open board has no outline on Edge.Cuts")
    old += keys
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
    drawn = len(new) // 2

    listed = sorted({t.value for t in labels if t.value not in names}
                    | {n for n, _ in groups if n not in empty},
                    key=str.casefold)
    k = BoardText()
    k.layer = BoardLayer.BL_Cmts_User
    k.value = "\n".join([KEY, *listed])
    k.position = Vector2.from_xy(min(q.pos.x for q in edge) - KEY_GAP,
                                 min(q.pos.y for q in edge))
    k.attributes.size = Vector2.from_xy(NM, NM)
    k.attributes.stroke_width = 150_000
    k.attributes.horizontal_alignment = HA_LEFT
    k.attributes.vertical_alignment = VA_TOP
    # the API's defaults draw every line on the first: one line, spacing 0
    k.attributes.multiline = True
    k.attributes.line_spacing = 1.0
    new.append(k)
    b.create_items(new)

    line = (f"{drawn} boxes drawn on User.Comments; contents list "
            f"{len(listed)} labels")
    if old:
        line += f", {len(old)} old items replaced"
    if empty:
        line += "; no footprints on the board: " + ", ".join(empty)
    print(line)


if __name__ == "__main__":
    main()
