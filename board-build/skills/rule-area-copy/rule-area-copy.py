#!/usr/bin/env python3
"""rule-area-copy — a new rule area with the settings of an existing one,
on a saved .kicad_pcb, headless.

    rule-area-copy.py <board> --from NAME [--uuid PREFIX] --name NEW
                      (--rect X0 Y0 X1 Y1 | --poly X Y X Y X Y ...
                       | --around ITEM ... --grow MM [--hull union|convex|rect])
                      [--dry-run]

Settings: everything of the rule area named NAME — layers, keepouts,
priority, placement source — copied whole; only the name and the outline
are new. Several rule areas named NAME are one source when their
settings agree, refused when they differ; `--uuid` then picks one by the
uuid prefix board-read prints.

Outline:

- `--rect`: the rectangle between two corners, mm
- `--poly`: the polygon through three or more points, mm, in order
- `--around`: the outline of items grown by `--grow` mm. An item is `REF`,
  a footprint's courtyard, its pads when it has none; `REF.PAD`, a pad;
  `net:PAT`, every track and via of the nets the pattern names
  - `--hull union`, default: the union of the items' own shapes, each
    grown, round corners. Holes are dropped. KiCad holds one outline per
    zone: pieces that do not touch become one rule area each, all named NEW
  - `--hull convex`: the convex hull of that union
  - `--hull rect`: the union of each item's bounding box grown; edges
    on the axes only; pieces as for union

KiCad must not be running. The board is backed up, saved to a temporary
file in a worker, checked by kicad-cli and then replaced — board-read.py.
"""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "board_read", Path(__file__).resolve().parents[1] / "board-read" / "board-read.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

TOOL = "rule-area-copy"
MAX_ERR = 5000   # nm, arc approximation


def parser():
    ap = br.edit_parser(TOOL, __doc__)
    ap.add_argument("--from", dest="source", required=True)
    ap.add_argument("--uuid", help="uuid prefix: one of several areas "
                    "named NAME")
    ap.add_argument("--name", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--rect", nargs=4, type=float)
    g.add_argument("--poly", nargs="+", type=float)
    g.add_argument("--around", nargs="+")
    ap.add_argument("--grow", type=float, default=0.0)
    ap.add_argument("--hull", choices=["union", "convex", "rect"],
                    default="union")
    return ap


def settings(b, z):
    return (br.layer_names(b, z.GetLayerSet()), z.GetDoNotAllowTracks(),
            z.GetDoNotAllowVias(), z.GetDoNotAllowPads(),
            z.GetDoNotAllowFootprints(), z.GetDoNotAllowZoneFills(),
            z.GetAssignedPriority(), z.GetPlacementAreaEnabled(),
            z.GetPlacementAreaSource())


def poly_of(points):
    p = br.pcb()
    ps = p.SHAPE_POLY_SET()
    ps.NewOutline()
    for x, y in points:
        ps.Append(br.iu(x), br.iu(y))
    return ps


def rect_poly(x0, y0, x1, y1):
    return poly_of([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def item_shapes(b, spec):
    """(polygon set of the item's copper or courtyard, its bounding boxes)."""
    p = br.pcb()
    out = []
    if spec.startswith("net:"):
        pat = spec[4:]
        tracks = [t for t in b.GetTracks() if br.match(t.GetNetname(), [pat])]
        if not tracks:
            raise br.Bad(TOOL, f"no track or via on a net matching {pat!r}")
        for t in tracks:
            ps = p.SHAPE_POLY_SET()
            lid = t.TopLayer() if br.is_via(t) else t.GetLayer()
            t.TransformShapeToPolygon(ps, lid, 0, MAX_ERR, p.ERROR_OUTSIDE)
            out.append((ps, t.GetBoundingBox()))
        return out
    if "." in spec and not any(f.GetReference() == spec
                               for f in b.GetFootprints()):
        q = br.pad(TOOL, b, spec)
        ps = p.SHAPE_POLY_SET()
        q.TransformShapeToPolygon(ps, q.GetPrincipalLayer(), 0, MAX_ERR,
                                  p.ERROR_OUTSIDE)
        return [(ps, q.GetBoundingBox())]
    fp = br.footprint(TOOL, b, spec)
    side = p.B_CrtYd if fp.IsFlipped() else p.F_CrtYd
    cy = fp.GetCourtyard(side)
    if cy.OutlineCount():
        return [(cy.CloneDropTriangulation(), fp.GetBoundingBox(False))]
    for q in fp.Pads():
        ps = p.SHAPE_POLY_SET()
        q.TransformShapeToPolygon(ps, q.GetPrincipalLayer(), 0, MAX_ERR,
                                  p.ERROR_OUTSIDE)
        out.append((ps, q.GetBoundingBox()))
    if not out:
        raise br.Bad(TOOL, f"{spec} has no courtyard and no pads")
    return out


def convex(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 0:
            hi.pop()
        hi.append(q)
    return lo[:-1] + hi[:-1]


def outline(b, a):
    p = br.pcb()
    if a.rect:
        x0, y0, x1, y1 = br.box_arg(a.rect)
        if x0 == x1 or y0 == y1:
            raise br.Bad(TOOL, "the rectangle has no area")
        return rect_poly(x0, y0, x1, y1), "rectangle"
    if a.poly:
        if len(a.poly) % 2 or len(a.poly) < 6:
            raise br.Bad(TOOL, "--poly takes three or more X Y pairs")
        return poly_of(list(zip(a.poly[0::2], a.poly[1::2]))), "polygon"
    if a.grow < 0:
        raise br.Bad(TOOL, "--grow is not negative")
    grow = br.iu(a.grow)
    shapes = [s for spec in a.around for s in item_shapes(b, spec)]
    acc = p.SHAPE_POLY_SET()
    for ps, bb in shapes:
        if a.hull == "rect":
            piece = rect_poly(br.mm(bb.GetLeft() - grow), br.mm(bb.GetTop() - grow),
                              br.mm(bb.GetRight() + grow),
                              br.mm(bb.GetBottom() + grow))
        else:
            piece = ps.CloneDropTriangulation()
            if grow:
                piece.Inflate(grow, p.CORNER_STRATEGY_ROUND_ALL_CORNERS,
                              MAX_ERR)
        acc.BooleanAdd(piece)
    acc.Simplify()
    res = p.SHAPE_POLY_SET()
    if a.hull == "convex":
        pts = []
        for i in range(acc.OutlineCount()):
            o = acc.COutline(i)
            pts += [(o.CPoint(j).x, o.CPoint(j).y) for j in range(o.PointCount())]
        res.NewOutline()
        for x, y in convex(pts):
            res.Append(x, y)
    else:
        for i in range(acc.OutlineCount()):   # outlines only: holes dropped
            o = acc.COutline(i)
            res.NewOutline()
            for j in range(o.PointCount()):
                res.Append(o.CPoint(j).x, o.CPoint(j).y)
    return res, f"{a.hull} of {len(shapes)} shapes grown {br.fmt(a.grow)} mm"


def edit(b, a):
    p = br.pcb()
    src = [z for z in b.Zones() if z.GetIsRuleArea()
           and z.GetZoneName() == a.source]
    if not src:
        raise br.Bad(TOOL, f"no rule area named {a.source!r}")
    if a.uuid:
        src = [z for z in src if z.m_Uuid.AsString().startswith(a.uuid)]
        if len(src) != 1:
            raise br.Bad(TOOL, f"{len(src)} rule areas named {a.source!r} "
                         f"have a uuid starting {a.uuid!r}; need one")
    if len({settings(b, z) for z in src}) > 1:
        raise br.Bad(TOOL, f"{len(src)} rule areas named {a.source!r} differ "
                     f"in their settings; pick one with --uuid, its uuid "
                     f"prefix from board-read rule-areas")
    if any(z.GetZoneName() == a.name for z in b.Zones()):
        raise br.Bad(TOOL, f"a zone or rule area named {a.name!r} exists")
    shape, how = outline(b, a)
    if shape.OutlineCount() == 0 or shape.Area() <= 0:
        raise br.Bad(TOOL, "the outline has no area")
    rows = []
    n = shape.OutlineCount()
    for k in range(n):   # a zone holds one outline: one area per piece
        z = src[0].Duplicate(False).Cast()
        z.SetZoneName(a.name)
        z.Outline().RemoveAllContours()
        z.Outline().AddOutline(shape.COutline(k))
        z.SetNeedRefill(False)
        b.Add(z)
        s = settings(b, z)
        if s != settings(b, src[0]):
            raise br.Bad(TOOL, "the copy's settings differ from the source's")
        rows.append(["add rule area", a.name, s[0], br.keepouts(z), s[6],
                     how + (f", piece {k + 1} of {n}" if n > 1 else ""),
                     f"{shape.COutline(k).PointCount()} points",
                     br.bbox_text(z.GetBoundingBox())])
    notes = [f"settings copied from {a.source!r}"
             + (f", {len(src)} areas of that name agree" if len(src) > 1
                else "")]
    return {"header": ["change", "name", "layers", "keepouts", "priority",
                       "outline", "shape", "bbox"],
            "rows": rows, "notes": notes}


if __name__ == "__main__":
    br.run_edit(TOOL, Path(__file__).resolve(), parser(), edit)
