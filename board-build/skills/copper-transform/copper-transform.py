#!/usr/bin/env python3
"""copper-transform — select a group of board items and translate, rotate
or mirror them, on a saved .kicad_pcb, headless. Nets are kept.

    copper-transform.py <board> [--ref PAT ...] [--net PAT ...] [--name PAT ...]
                        [--box X0 Y0 X1 Y1] [--kind K ...]
                        (--move DX DY | --rotate DEG --about X Y
                         | --mirror-x X | --mirror-y Y) [--dry-run]

Selection. Kinds: footprint, track, via, zone, rule-area; `--kind` limits
to those, default all. Each criterion applies to the kinds it can name:

- `--ref`: footprints, by reference
- `--net`: tracks, vias and zones, by net name
- `--name`: zones and rule areas, by name
- `--box`: every kind, the item's bounding box wholly inside

An item is selected when every criterion that applies to its kind matches,
and at least one does. Patterns are shell wildcards. Nothing selected is
refused.

Transform. `--move` by (DX, DY) mm; `--rotate` DEG counter-clockwise on
screen about (X, Y); `--mirror-x X` mirrors about the vertical line x = X,
`--mirror-y Y` about the horizontal line y = Y. Tracks, vias and zone
outlines mirror exactly. A footprint cannot mirror on its own side: its
position and rotation are mirrored, its pads are not, so tracks into an
asymmetric footprint no longer meet its pads — the table says so.

Zone fills move with their zones; fills are not recomputed. Refill in
KiCad, B.

Run `--dry-run` first: it lists every selected item and where it goes.
KiCad must not be running. The board is backed up, saved to a temporary
file in a worker, checked by kicad-cli and then replaced — board-read.py.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "copper-transform"
KINDS = ["footprint", "track", "via", "zone", "rule-area"]
APPLIES = {"ref": {"footprint"}, "net": {"track", "via", "zone"},
           "name": {"zone", "rule-area"}, "box": set(KINDS)}


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("--ref", nargs="+")
    ap.add_argument("--net", nargs="+")
    ap.add_argument("--name", nargs="+")
    ap.add_argument("--box", nargs=4, type=float)
    ap.add_argument("--kind", nargs="+", choices=KINDS)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--move", nargs=2, type=float, metavar=("DX", "DY"))
    g.add_argument("--rotate", type=float, metavar="DEG")
    g.add_argument("--mirror-x", type=float, metavar="X")
    g.add_argument("--mirror-y", type=float, metavar="Y")
    ap.add_argument("--about", nargs=2, type=float, metavar=("X", "Y"))
    return ap


def selected(b, a, item):
    k = br.kind(item)
    if a.kind and k not in a.kind:
        return False
    tests = []
    if a.ref and k in APPLIES["ref"]:
        tests.append(br.match(item.GetReference(), a.ref))
    if a.net and k in APPLIES["net"]:
        tests.append(br.match(item.GetNetname(), a.net))
    if a.name and k in APPLIES["name"]:
        tests.append(br.match(item.GetZoneName(), a.name))
    if a.box and k in APPLIES["box"]:
        bb = (item.GetBoundingBox(False) if k == "footprint"
              else item.GetBoundingBox())
        tests.append(br.bbox_in(a.box, bb))
    return bool(tests) and all(tests)


def edit(b, a):
    p = br.pcb()
    if not (a.ref or a.net or a.name or a.box):
        raise br.Bad(TOOL, "no selection: give --ref, --net, --name or --box")
    if a.rotate is not None and a.about is None:
        raise br.Bad(TOOL, "--rotate needs --about X Y")
    if a.box:
        a.box = br.box_arg(a.box)
    items = ([f for f in b.GetFootprints()] + [t for t in b.GetTracks()]
             + [z for z in b.Zones()])
    sel = [i for i in items if selected(b, a, i)]
    if not sel:
        raise br.Bad(TOOL, "the selection holds nothing")
    rows, notes = [], []
    mirrored_fp = []
    for i in sel:
        k = br.kind(i)
        was = br.place_of(i)
        name = br.describe(b, i)
        if a.move is not None:
            i.Move(br.vec(*a.move))
        elif a.rotate is not None:
            i.Rotate(br.vec(*a.about), br.angle(a.rotate))
        else:
            vertical = a.mirror_x is not None
            c = br.vec(a.mirror_x, 0) if vertical else br.vec(0, a.mirror_y)
            if k == "footprint":
                pos = i.GetPosition()
                if vertical:
                    i.SetPosition(p.VECTOR2I(2 * c.x - pos.x, pos.y))
                    i.SetOrientationDegrees(
                        br.norm_deg(180.0 - i.GetOrientationDegrees()))
                else:
                    i.SetPosition(p.VECTOR2I(pos.x, 2 * c.y - pos.y))
                    i.SetOrientationDegrees(
                        br.norm_deg(-i.GetOrientationDegrees()))
                mirrored_fp.append(i.GetReference())
            else:
                i.Mirror(c, p.FLIP_DIRECTION_LEFT_RIGHT if vertical
                         else p.FLIP_DIRECTION_TOP_BOTTOM)
        rows.append([k, name, was, br.place_of(i), i.GetNetname()
                     if k != "footprint" and k != "rule-area" else ""])
    if mirrored_fp:
        notes.append("footprints mirrored by position and rotation only, "
                     "pads not mirrored: " + " ".join(mirrored_fp))
    if any(br.kind(i) == "zone" for i in sel):
        notes.append("zone fills moved with their zones and are not "
                     "recomputed; refill in KiCad")
    count = {}
    for i in sel:
        count[br.kind(i)] = count.get(br.kind(i), 0) + 1
    notes.append("selected: " + "; ".join(f"{v} {k}" for k, v in
                                          sorted(count.items())))
    return {"header": ["kind", "item", "before", "after", "net"],
            "rows": rows, "notes": notes}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
