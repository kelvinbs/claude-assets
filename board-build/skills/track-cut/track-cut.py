#!/usr/bin/env python3
"""track-cut — remove the stretch of a net's straight tracks between two
points on their line, on a saved .kicad_pcb, headless. What lies outside
the stretch is kept, trimmed to its ends.

    track-cut.py <board> --net NET --from X Y --to X Y [--layer L] [--dry-run]

Every straight track on the net (and on `--layer` when given) whose both
ends lie within 0.01 mm of the line through the two points is cut: the
part between the points goes, the parts outside stay. A track wholly
between them goes. Arcs and vias are not touched. Nothing cut is refused.

Run `--dry-run` first. KiCad must not be running. The board is backed up,
saved to a temporary file in a worker, checked by kicad-cli and then
replaced — board-read.py.
"""

import importlib.util
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "track-cut"
TOL = 0.01


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("--net", required=True)
    ap.add_argument("--from", dest="p", nargs=2, type=float, required=True)
    ap.add_argument("--to", dest="q", nargs=2, type=float, required=True)
    ap.add_argument("--layer")
    return ap


def edit(b, a):
    p = br.pcb()
    (px, py), (qx, qy) = a.p, a.q
    L = math.hypot(qx - px, qy - py)
    if L <= 0:
        raise br.Bad(TOOL, "--from and --to are one point")
    ux, uy = (qx - px) / L, (qy - py) / L
    lid = br.layer_id(TOOL, b, a.layer) if a.layer else None

    def off(x, y):
        return abs((x - px) * uy - (y - py) * ux)

    def s_of(x, y):
        return (x - px) * ux + (y - py) * uy

    def at(s):
        return p.VECTOR2I(br.iu(px + ux * s), br.iu(py + uy * s))

    rows = []
    for t in list(b.GetTracks()):
        if br.is_via(t) or br.is_arc(t) or t.GetNetname() != a.net:
            continue
        if lid is not None and t.GetLayer() != lid:
            continue
        sx, sy = br.mm(t.GetStart().x), br.mm(t.GetStart().y)
        ex, ey = br.mm(t.GetEnd().x), br.mm(t.GetEnd().y)
        if off(sx, sy) > TOL or off(ex, ey) > TOL:
            continue
        s0, s1 = s_of(sx, sy), s_of(ex, ey)
        lo, hi = min(s0, s1), max(s0, s1)
        if hi <= 0 or lo >= L:
            continue
        was = br.place_of(t)
        keep = [(lo, min(hi, 0.0)), (max(lo, L), hi)]
        keep = [(u, v) for u, v in keep if v - u > 1e-6]
        layer, width, net = t.GetLayer(), t.GetWidth(), t.GetNet()
        b.Delete(t)
        rows.append(["remove", a.net, b.GetLayerName(layer), was,
                     br.fmt(br.mm(width))])
        for u, v in keep:
            n = p.PCB_TRACK(b)
            n.SetStart(at(u))
            n.SetEnd(at(v))
            n.SetLayer(layer)
            n.SetWidth(width)
            n.SetNet(net)
            b.Add(n)
            rows.append(["keep", a.net, b.GetLayerName(layer),
                         br.place_of(n), br.fmt(br.mm(width))])
    if not rows:
        raise br.Bad(TOOL, f"no straight track of {a.net} lies on the line "
                     "between the two points")
    return {"header": ["change", "net", "layer", "track", "width"],
            "rows": rows, "notes": []}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
