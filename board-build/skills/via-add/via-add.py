#!/usr/bin/env python3
"""via-add — add through vias on a net at points, along a line, or as a
fence around rule areas, on a saved .kicad_pcb, headless. A via that would
clash is skipped, not placed.

    via-add.py <board> --net NET
               (--at X Y [X Y ...]
                | --along X Y X Y [X Y ...] [--closed]
                | --around NAME ... [--box X0 Y0 X1 Y1] --grow G)
               [--pitch P] [--diameter D --drill H] [--clearance C]
               [--spacing S] [--cross-zone-net PAT ...]
               [--in-courtyard REF ...] [--dry-run]

Where. `--at`: one via per point. `--along`: points every `--pitch` mm
along the polyline, `--closed` back to its first point. `--around`: the
rule areas whose names match NAME ... (those wholly inside `--box` when given), their
outlines united and grown by G mm, points every `--pitch` mm along each
resulting outline.

Size. `--diameter` and `--drill` mm; default the board's current via size.

Skipped, each counted in the notes:

- off the board: the via within `--clearance` + 0.3 mm of the edge
- crowded: centre within `--spacing` mm (default 0.8 × pitch, or the
  diameter + clearance) of another via, placed or new
- clash: within `--clearance` mm (default the board's minimum clearance)
  of a pad, track, via or footprint copper of another net or of no net,
  on any copper layer
- inside a footprint's courtyard, except those named by `--in-courtyard`,
  as for vias that ground a part's own pads
- inside a rule area that keeps vias out
- inside the outline of a zone of another net, except nets matching
  `--cross-zone-net`, whose zones a refill clears around the via

Run `--dry-run` first. KiCad must not be running. The board is backed up,
saved to a temporary file in a worker, checked by kicad-cli and then
replaced — board-read.py. Zones are not refilled: zone-fill.
"""

import importlib.util
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "via-add"
MAX_ERR = 5000
EDGE = 0.3


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("--net", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--at", nargs="+", type=float)
    g.add_argument("--along", nargs="+", type=float)
    g.add_argument("--around", nargs="+")
    ap.add_argument("--closed", action="store_true")
    ap.add_argument("--box", nargs=4, type=float)
    ap.add_argument("--grow", type=float)
    ap.add_argument("--pitch", type=float)
    ap.add_argument("--diameter", type=float)
    ap.add_argument("--drill", type=float)
    ap.add_argument("--clearance", type=float)
    ap.add_argument("--spacing", type=float)
    ap.add_argument("--cross-zone-net", nargs="+", default=[])
    ap.add_argument("--in-courtyard", nargs="+", default=[])
    return ap


def pairs(tool, v, what):
    if len(v) % 2:
        raise br.Bad(tool, f"{what}: an odd count of numbers")
    return [(v[i], v[i + 1]) for i in range(0, len(v), 2)]


def walk(pts, pitch):
    """Points every pitch mm along a polyline, the first point included."""
    out, carry = [], 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = math.hypot(x1 - x0, y1 - y0)
        s = carry
        while s <= n + 1e-9:
            f = s / n if n else 0.0
            out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
            s += pitch
        carry = s - n
    return out


def chain_points(ch):
    pts = [(br.mm(ch.CPoint(k).x), br.mm(ch.CPoint(k).y))
           for k in range(ch.PointCount())]
    return pts + pts[:1]


def candidates(b, a):
    p = br.pcb()
    if a.at is not None:
        return pairs(TOOL, a.at, "--at")
    if a.pitch is None or a.pitch <= 0:
        raise br.Bad(TOOL, "--along and --around need --pitch > 0")
    if a.along is not None:
        pts = pairs(TOOL, a.along, "--along")
        if len(pts) < 2:
            raise br.Bad(TOOL, "--along needs two points or more")
        if a.closed:
            pts = pts + pts[:1]
        return walk(pts, a.pitch)
    if a.grow is None:
        raise br.Bad(TOOL, "--around needs --grow")
    box = br.box_arg(a.box) if a.box else None
    areas = [z for z in b.Zones() if z.GetIsRuleArea()
             and br.match(z.GetZoneName(), a.around)
             and (box is None or br.bbox_in(box, z.GetBoundingBox()))]
    if not areas:
        raise br.Bad(TOOL, f"no rule area named {' '.join(a.around)}"
                     + (" inside the box" if box else ""))
    u = p.SHAPE_POLY_SET()
    for z in areas:
        u.BooleanAdd(z.Outline())
    u.Simplify()
    if a.grow:
        u.Inflate(br.iu(a.grow), p.CORNER_STRATEGY_ROUND_ALL_CORNERS,
                  MAX_ERR)
    out = []
    for i in range(u.OutlineCount()):
        out += walk(chain_points(u.COutline(i)), a.pitch)
    return out


def poly(item, layer):
    p = br.pcb()
    ps = p.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(ps, layer, 0, MAX_ERR, p.ERROR_OUTSIDE)
    return ps


def obstacles(b, a, net):
    """(kind, polygon set, bbox in mm) of everything a new via must clear."""
    p = br.pcb()
    cu = [i for i in b.GetEnabledLayers().CuStack()]
    out = []

    def add(kind, ps):
        if ps.OutlineCount():
            bb = ps.BBox()
            out.append((kind, ps, (br.mm(bb.GetLeft()), br.mm(bb.GetTop()),
                                   br.mm(bb.GetRight()), br.mm(bb.GetBottom()))))
    for f in b.GetFootprints():
        for q in f.Pads():
            if q.GetNetname() == net:
                continue
            for lid in cu:
                if q.IsOnLayer(lid):
                    add("clash", poly(q, lid))
        for g in f.GraphicalItems():
            if g.GetClass() == "PCB_SHAPE" and g.IsOnCopperLayer() \
                    and g.GetNetname() != net:
                add("clash", poly(g, g.GetLayer()))
        side = p.B_CrtYd if f.IsFlipped() else p.F_CrtYd
        cy = f.GetCourtyard(side)
        if cy.OutlineCount() and f.GetReference() not in a.in_courtyard:
            add("courtyard", cy.CloneDropTriangulation())
    for t in b.GetTracks():
        if t.GetNetname() == net:
            continue
        add("clash", poly(t, t.TopLayer() if br.is_via(t) else t.GetLayer()))
    for d in b.GetDrawings():
        if d.GetClass() == "PCB_SHAPE" and d.IsOnCopperLayer() \
                and d.GetNetname() != net:
            add("clash", poly(d, d.GetLayer()))
    for z in b.Zones():
        if z.GetIsRuleArea():
            if z.GetDoNotAllowVias():
                add("keep-out", z.Outline().CloneDropTriangulation())
        elif z.GetNetname() != net and not br.match(z.GetNetname(),
                                                    a.cross_zone_net):
            add("zone", z.Outline().CloneDropTriangulation())
    return out


def edit(b, a):
    p = br.pcb()
    net = b.FindNet(a.net)
    if net is None or not a.net:
        raise br.Bad(TOOL, f"no net {a.net!r} on the board")
    ds = b.GetDesignSettings()
    dia = a.diameter if a.diameter is not None else br.mm(ds.GetCurrentViaSize())
    drl = a.drill if a.drill is not None else br.mm(ds.GetCurrentViaDrill())
    if dia <= 0 or drl <= 0 or drl >= dia:
        raise br.Bad(TOOL, f"via ⌀ {dia}, drill {drl}")
    clr = a.clearance if a.clearance is not None else br.mm(
        ds.m_MinClearance)
    sp = a.spacing if a.spacing is not None else (
        0.8 * a.pitch if a.pitch else dia + clr)
    r = dia / 2
    edge = p.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(edge, False)
    inner = edge.CloneDropTriangulation()
    inner.Inflate(-br.iu(r + EDGE), p.CORNER_STRATEGY_ROUND_ALL_CORNERS,
                  MAX_ERR)
    obs = obstacles(b, a, a.net)
    vias = [(br.mm(t.GetPosition().x), br.mm(t.GetPosition().y))
            for t in b.GetTracks() if br.is_via(t)]
    skipped = {}
    rows = []
    for x, y in candidates(b, a):
        v = p.VECTOR2I(br.iu(x), br.iu(y))
        why = None
        if edge.OutlineCount() and not inner.Contains(v):
            why = "off the board"
        elif any(math.hypot(x - vx, y - vy) < sp for vx, vy in vias):
            why = "crowded"
        else:
            for kind, ps, (x0, y0, x1, y1) in obs:
                m = r + clr if kind == "clash" else (
                    r if kind != "courtyard" else 0.0)
                if x < x0 - m or x > x1 + m or y < y0 - m or y > y1 + m:
                    continue
                if ps.SquaredDistance(v) < br.iu(m) ** 2 or ps.Contains(v):
                    why = kind
                    break
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        t = p.PCB_VIA(b)
        t.SetViaType(p.VIATYPE_THROUGH)
        t.SetLayerPair(p.F_Cu, p.B_Cu)
        t.SetPosition(v)
        t.SetWidth(br.iu(dia))
        t.SetDrill(br.iu(drl))
        t.SetNet(net)
        b.Add(t)
        vias.append((x, y))
        rows.append(["add via", a.net, br.xy(v), f"⌀ {br.fmt(dia)}, "
                     f"drill {br.fmt(drl)}"])
    notes = [f"clearance {br.fmt(clr)} mm, spacing {br.fmt(sp)} mm"]
    if skipped:
        notes.append("skipped: " + "; ".join(f"{v} {k}" for k, v in
                                            sorted(skipped.items())))
    notes.append("zones are not refilled; zone-fill")
    return {"header": ["change", "net", "at", "size"], "rows": rows,
            "notes": notes}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
