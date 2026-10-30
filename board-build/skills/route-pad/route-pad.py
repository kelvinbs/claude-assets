#!/usr/bin/env python3
"""route-pad — a straight or two-segment orthogonal track between two pads,
on one layer, on a saved .kicad_pcb, headless.

    route-pad.py <board> REF.PAD REF.PAD [--layer L] [--width MM]
                 [--shape auto|straight|hv|vh]
                 [--neck W L [--neck-at a|b]] [--dry-run]

The net is the pads' own; two pads on different nets, or a pad on none,
are refused. The layer is the copper layer both pads are on, `--layer`
when they share more than one. The width is the net class's track width,
from the project file, unless `--width`.

Shape: `straight`, one segment centre to centre; `hv`, horizontal then
vertical; `vh`, vertical then horizontal; `auto`, straight when the pads
share an x or a y, else `hv`.

A pad is `REF.PAD`, or `REF.PAD@X,Y` when several pads carry the number:
the one nearest (X, Y).

Neck: `--neck W L` narrows the route to W mm over its last L mm into pad
b, or its first L mm out of pad a with `--neck-at a`: the track stops L
short and a filled copper taper on the net, from the track width to W,
spans the rest. The necked segment must be at least L long.

KiCad must not be running. The board is backed up, saved to a temporary
file in a worker, checked by kicad-cli and then replaced — board-read.py.
"""

import importlib.util
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "route-pad"


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("a", metavar="REF.PAD")
    ap.add_argument("b", metavar="REF.PAD")
    ap.add_argument("--layer")
    ap.add_argument("--width", type=float)
    ap.add_argument("--shape", choices=["auto", "straight", "hv", "vh"],
                    default="auto")
    ap.add_argument("--neck", nargs=2, type=float, metavar=("W", "L"))
    ap.add_argument("--neck-at", choices=["a", "b"], default="b")
    return ap


def taper(b, net, lid, u, v, w0, w1):
    """A filled copper quadrilateral on the net, width w0 at u to w1 at v."""
    p = br.pcb()
    dx, dy = v.x - u.x, v.y - u.y
    n = math.hypot(dx, dy)
    nx, ny = -dy / n, dx / n
    h0, h1 = br.iu(w0) / 2, br.iu(w1) / 2
    ps = p.SHAPE_POLY_SET()
    ps.NewOutline()
    for x, y in ((u.x + nx * h0, u.y + ny * h0), (v.x + nx * h1, v.y + ny * h1),
                 (v.x - nx * h1, v.y - ny * h1), (u.x - nx * h0, u.y - ny * h0)):
        ps.Append(int(round(x)), int(round(y)))
    s = p.PCB_SHAPE(b)
    s.SetShape(p.SHAPE_T_POLY)
    s.SetPolyShape(ps)
    s.SetFilled(True)
    s.SetWidth(0)
    s.SetLayer(lid)
    s.SetNet(net)
    b.Add(s)


def edit(b, a):
    p = br.pcb()
    pa, pb = br.pad(TOOL, b, a.a), br.pad(TOOL, b, a.b)
    na, nb = pa.GetNetname(), pb.GetNetname()
    if not na or not nb:
        raise br.Bad(TOOL, f"{a.a if not na else a.b} is on no net")
    if na != nb:
        raise br.Bad(TOOL, f"{a.a} is on {na}, {a.b} on {nb}: different nets")
    common = [i for i in pa.GetLayerSet().CuStack() if pb.IsOnLayer(i)]
    if a.layer:
        lid = br.layer_id(TOOL, b, a.layer)
        if lid not in common:
            raise br.Bad(TOOL, f"{a.layer} is not a copper layer of both pads")
    elif len(common) == 1:
        lid = common[0]
    elif not common:
        raise br.Bad(TOOL, "the pads share no copper layer")
    else:
        raise br.Bad(TOOL, "the pads share " + ", ".join(
            b.GetLayerName(i) for i in common) + "; give --layer")
    if a.width is not None:
        cls, w = "given", a.width
    else:
        cls, w = br.netclass_width(b, a.board, na)
    if w <= 0:
        raise br.Bad(TOOL, f"width {w} mm")
    s, e = pa.GetPosition(), pb.GetPosition()
    if (s.x, s.y) == (e.x, e.y):
        raise br.Bad(TOOL, "the two pads share a centre; nothing to route")
    shape = a.shape
    if shape == "auto":
        shape = "straight" if s.x == e.x or s.y == e.y else "hv"
    pts = [s, e]
    if shape == "hv" and s.x != e.x and s.y != e.y:
        pts = [s, p.VECTOR2I(e.x, s.y), e]
    elif shape == "vh" and s.x != e.x and s.y != e.y:
        pts = [s, p.VECTOR2I(s.x, e.y), e]
    net = b.FindNet(na)
    rows = []
    segs = list(zip(pts, pts[1:]))
    neck = None
    if a.neck:
        nw, nl = a.neck
        if nw <= 0 or nl <= 0:
            raise br.Bad(TOOL, f"neck {nw} mm over {nl} mm")
        i = len(segs) - 1 if a.neck_at == "b" else 0
        u, v = segs[i]
        n = br.mm(int(math.hypot(v.x - u.x, v.y - u.y)))
        if n < nl:
            raise br.Bad(TOOL, f"the necked segment is {br.fmt(n)} mm, "
                         f"shorter than the neck {br.fmt(nl)} mm")
        f = nl / n
        if a.neck_at == "b":
            m = p.VECTOR2I(int(round(v.x - (v.x - u.x) * f)),
                           int(round(v.y - (v.y - u.y) * f)))
            segs[i] = (u, m)
            neck = (m, v, w, nw)
        else:
            m = p.VECTOR2I(int(round(u.x + (v.x - u.x) * f)),
                           int(round(u.y + (v.y - u.y) * f)))
            segs[i] = (m, v)
            neck = (m, u, w, nw)
    for u, v in segs:
        t = p.PCB_TRACK(b)
        t.SetStart(u)
        t.SetEnd(v)
        t.SetLayer(lid)
        t.SetWidth(br.iu(w))
        t.SetNet(net)
        b.Add(t)
        rows.append(["add track", na, b.GetLayerName(lid), br.xy(u), br.xy(v),
                     br.fmt(w)])
    if neck:
        m, e, w0, w1 = neck
        taper(b, net, lid, m, e, w0, w1)
        rows.append(["add taper", na, b.GetLayerName(lid), br.xy(m), br.xy(e),
                     f"{br.fmt(w0)} → {br.fmt(w1)}"])
    return {"header": ["change", "net", "layer", "start", "end", "width"],
            "rows": rows,
            "notes": [f"{a.a} → {a.b}, {shape}, width {br.fmt(w)} mm "
                      f"({'net class ' + cls if cls != 'given' else '--width'})",
                      "no clearance check is made by the tool; read the DRC "
                      "table"]}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
