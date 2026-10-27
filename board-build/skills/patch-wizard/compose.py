"""Compose a printed part from footprints the project already has.

A printed mixer, a feed network, any assembly of printed elements: the
elements are the project's own footprints — a Wilkinson, a rat-race ring, a
diode package — read from its library, never redrawn. A composition file
places them, says which ports join, and which points become the part's pads.
This script reads both and writes one footprint.

  python3 compose.py <design-dir> <composition.json>

Run with KiCad's Python. It reads `lib/<nickname>.pretty/` for the elements
and writes `lib/<nickname>.pretty/<name>.kicad_mod`.

The composition, `design/compositions/<name>.json`:

  name          the footprint written
  library       the .pretty the elements come from, relative to design/
  layer         the copper layer the part is on
  width         the line width the joins are drawn in, mm
  lead          straight line off every port before a join turns, mm
  elements      {id: {footprint, at [x, y], rot, mirror}}: copper elements.
                rot in degrees anticlockwise, mirror about x after rot
  packages      {id: {footprint, port, pin, toward, lead}}: parts that
                solder to the copper — a diode. Drawn as outline on F.Fab
                with a land under each named pin; placed with `pin` at `lead`
                beyond `port` and `toward` straight on from it
  joins         [{from, to, extra, side}]: a line between two ports. `extra`
                lengthens it by a U bend, mm, to `side` +1 or -1
  stubs         [{from, dir, length, width, pad}]: a line off a port to a
                land, a shorted stub when the land takes a via
  pads          {number: point or [points]}: the part's pads. A point
                is `<element>.<port>` with a land of the join width, or
                `<package>.<pin>` with that pin's own land, turned with the
                package. A list puts the number at each point: two pieces of
                copper on one net that the part cannot join on its own layer,
                as the two diodes of a rat-race mixer meet at their IF across
                a feed. The board joins them; KiCad shows the ratsnest
  net_tie       the pad numbers one copper piece joins, for KiCad's DRC

A port is named by the element's id and either a pad number, where the
element numbers its ports, or a compass point N S E W of the composed part,
where the element is one net — a Wilkinson drawn as a single pad number.
The script prints every port it finds, and every join's length.
"""

import json
import math
import sys
from pathlib import Path

import pcbnew

MM = pcbnew.FromMM
OVERLAP = 0.05  # mm a land laps the copper it joins


def to_mm(v):
    return pcbnew.ToMM(v)


class Frame:
    """Native footprint coordinates to the composed part's, mm, y down.
    Rotation is anticlockwise as seen, mirror flips y after it."""

    def __init__(self, at, rot, mirror):
        self.x0, self.y0 = at
        self.c = math.cos(math.radians(rot))
        self.s = math.sin(math.radians(rot))
        self.m = -1.0 if mirror else 1.0

    def vec(self, x, y):
        X = x * self.c + y * self.s
        Y = -x * self.s + y * self.c
        return X, self.m * Y

    def pt(self, x, y):
        X, Y = self.vec(x, y)
        return self.x0 + X, self.y0 + Y


def polygons(pad, layer, outside=False):
    """A pad's copper as point lists, mm, native frame. `outside` traces a
    curved edge from outside, 0.1 um off: a land drawn so holds the part's
    own pad whole, within KiCad's 0.5 um DRC epsilon of it."""
    ps = pcbnew.SHAPE_POLY_SET()
    if outside:
        pad.TransformShapeToPolygon(ps, layer, 0, MM(0.0001),
                                    pcbnew.ERROR_OUTSIDE)
    else:
        pad.TransformShapeToPolygon(ps, layer, 0, MM(0.002),
                                    pcbnew.ERROR_INSIDE)
    out = []
    for i in range(ps.OutlineCount()):
        o = ps.Outline(i)
        out.append([(to_mm(o.CPoint(j).x), to_mm(o.CPoint(j).y))
                    for j in range(o.PointCount())])
    return out


def is_land(pad):
    """A land a part solders to carries mask; a printed line does not."""
    return pad.GetLayerSet().Contains(pcbnew.F_Mask) and \
        pad.GetShape(pcbnew.F_Cu) != pcbnew.PAD_SHAPE_CUSTOM


def compass(dx, dy):
    if abs(dx) > abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"


def element_ports(fp, layer, width):
    """Ports in the native frame: {name: ((x, y), (dx, dy))}.

    Numbered ports: each distinct pad number but the lands is a port at
    the pad's outer end, outward from the footprint's origin. One-net
    elements: each copper edge on the element's outline, one line width
    long, is a port, outward across that edge."""
    pads = [p for p in fp.Pads() if not is_land(p)]
    numbers = {p.GetNumber() for p in pads if p.GetNumber()}
    ports = {}
    if len(numbers) > 1:
        for p in pads:
            if not p.GetNumber():
                continue
            cx, cy = to_mm(p.GetPosition().x), to_mm(p.GetPosition().y)
            n = math.hypot(cx, cy)
            dx, dy = cx / n, cy / n
            half = to_mm(p.GetSize(pcbnew.F_Cu).x) / 2.0
            ports[p.GetNumber()] = ((cx + dx * half, cy + dy * half), (dx, dy))
        return ports, "numbered"
    polys = [poly for p in pads for poly in polygons(p, layer)]
    xs = [x for poly in polys for x, _ in poly]
    ys = [y for poly in polys for _, y in poly]
    box = {"W": min(xs), "E": max(xs), "N": min(ys), "S": max(ys)}
    found = []
    for poly in polys:
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            length = math.hypot(x2 - x1, y2 - y1)
            if abs(length - width) > 0.1 * width:
                continue
            for side, edge in box.items():
                on = (abs(x1 - edge) < 1e-3 and abs(x2 - edge) < 1e-3) \
                    if side in "WE" else \
                    (abs(y1 - edge) < 1e-3 and abs(y2 - edge) < 1e-3)
                if on:
                    d = {"W": (-1, 0), "E": (1, 0), "N": (0, -1),
                         "S": (0, 1)}[side]
                    found.append((((x1 + x2) / 2, (y1 + y2) / 2), d))
    return {i: f for i, f in enumerate(found)}, "one-net"


class Composer:

    def __init__(self, design, comp):
        self.design = Path(design)
        self.c = comp
        self.lib = str(self.design / comp["library"])
        self.layer = pcbnew.BOARD().GetLayerID(comp.get("layer", "F.Cu"))
        self.w = comp["width"]
        self.lead = comp.get("lead", 1.0)
        self.fp = pcbnew.FOOTPRINT(None)
        self.fp.SetFPID(pcbnew.LIB_ID("", comp["name"]))
        self.ports = {}
        self.points = {}
        self.lengths = []
        self.line = []
        self.tags = []   # per line piece: the package point its lead ends on
        self.tag = None

    def load(self, name):
        fp = pcbnew.FootprintLoad(self.lib, name)
        if fp is None:
            raise SystemExit(f"{name} is not in {self.lib}")
        return fp

    # copper ---------------------------------------------------------------

    def poly_pad(self, pts, number="", mask=False, line=False):
        """Copper as a custom pad on the part's layer. Unnumbered copper is
        line: kept until the part is built, then numbered by line_copper. A
        number makes a port KiCad gives a net."""
        if not number and not line:
            self.line.append(pts)
            self.tags.append(self.tag)
            return
        pad = pcbnew.PAD(self.fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetShape(self.layer, pcbnew.PAD_SHAPE_CUSTOM)
        pad.SetAnchorPadShape(self.layer, pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(self.layer, pcbnew.VECTOR2I(MM(0.01), MM(0.01)))
        seq = pcbnew.LSEQ()
        seq.push_back(self.layer)
        if mask:
            seq.push_back(pcbnew.F_Mask)
        pad.SetLayerSet(pcbnew.LSET(seq))
        ax, ay = pts[0]
        pad.SetPosition(pcbnew.VECTOR2I(MM(ax), MM(ay)))
        vv = pcbnew.VECTOR_VECTOR2I()
        for x, y in pts:
            vv.append(pcbnew.VECTOR2I(MM(x - ax), MM(y - ay)))
        pad.AddPrimitivePoly(self.layer, vv, 0, True)
        pad.SetNumber(str(number))
        self.fp.Add(pad)

    def line_copper(self):
        """The line as custom pads under the net tie's first number: line
        then belongs to the tie group, and KiCad's DRC skips clearance and
        shorts between pads of one group, as the project's own Wilkinson
        and ring do with one number. A copper shape would not: KiCad joins
        a shape only to the pads it touches itself, never through the
        shapes it touches.

        Every pad of another number is cut out of a piece, so line meets
        such a pad only at the pad's edge. A part soldered on a land, or a track ending on
        a port, then touches the line only on that edge, and KiCad excuses
        a net-tie contact only at a point on a tie pad of the same net."""
        tie = [n.strip() for n in self.c.get("net_tie", "").split(",")
               if n.strip()]
        first = tie[0] if tie else ""
        at = {}
        for n, pts in self.c["pads"].items():
            for pt in [pts] if isinstance(pts, str) else pts:
                at[pt] = n
        pads = {}
        for pad in self.fp.Pads():
            pads.setdefault(pad.GetNumber(), pcbnew.SHAPE_POLY_SET())
            pad.TransformShapeToPolygon(pads[pad.GetNumber()], self.layer, 0,
                                        MM(0.002), pcbnew.ERROR_OUTSIDE)
        cuts = {}
        for pts, tag in zip(self.line, self.tags):
            # a lead ending on a land is that land's copper: the part
            # soldered there then meets only copper of its own net
            number = at.get(tag, first) if at.get(tag) in tie else first
            piece = pcbnew.SHAPE_POLY_SET()
            o = pcbnew.SHAPE_LINE_CHAIN()
            for x, y in pts:
                o.Append(MM(x), MM(y))
            o.SetClosed(True)
            piece.AddOutline(o)
            if number not in cuts:
                # a piece laps its own number's pads, so it joins them
                cuts[number] = pcbnew.SHAPE_POLY_SET()
                for n, ps in pads.items():
                    if n != number:
                        cuts[number].Append(ps)
                cuts[number].Simplify()
            piece.BooleanSubtract(cuts[number])
            piece.Fracture()
            for i in range(piece.OutlineCount()):
                ol = piece.Outline(i)
                if ol.PointCount() < 3:
                    continue
                self.poly_pad([(to_mm(ol.CPoint(j).x), to_mm(ol.CPoint(j).y))
                               for j in range(ol.PointCount())],
                              number, line=True)

    def rect_pad(self, number, cx, cy, sx, sy, mask=False):
        pad = pcbnew.PAD(self.fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetShape(self.layer, pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetSize(self.layer, pcbnew.VECTOR2I(MM(sx), MM(sy)))
        seq = pcbnew.LSEQ()
        seq.push_back(self.layer)
        if mask:
            seq.push_back(pcbnew.F_Mask)
        pad.SetLayerSet(pcbnew.LSET(seq))
        pad.SetPosition(pcbnew.VECTOR2I(MM(cx), MM(cy)))
        pad.SetNumber(str(number))
        self.fp.Add(pad)

    def segment(self, a, b, w):
        """A straight run of line between two points, any angle, with a
        round joint at each end so runs meeting at an angle close."""
        (x1, y1), (x2, y2) = a, b
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1e-6:
            return 0.0
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        nx, ny = -uy * w / 2, ux * w / 2
        self.poly_pad([(x1 + nx, y1 + ny), (x2 + nx, y2 + ny),
                       (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)])
        for x, y in (a, b):
            self.poly_pad([(x + w / 2 * math.cos(k * math.pi / 8),
                            y + w / 2 * math.sin(k * math.pi / 8))
                           for k in range(16)])
        return L

    def path(self, pts, w):
        return sum(self.segment(a, b, w) for a, b in zip(pts, pts[1:]))

    # elements -------------------------------------------------------------

    def place_elements(self):
        for eid, e in self.c["elements"].items():
            fp = self.load(e["footprint"])
            fr = Frame(e["at"], e.get("rot", 0.0), e.get("mirror", False))
            for pad in fp.Pads():
                for poly in polygons(pad, self.layer):
                    pts = [fr.pt(x, y) for x, y in poly]
                    if is_land(pad):
                        # a land keeps its mask; it is placed as a pad of
                        # its own below, under the number the pads give it
                        continue
                    self.poly_pad(pts)
                if is_land(pad):
                    x, y = fr.pt(to_mm(pad.GetPosition().x),
                                 to_mm(pad.GetPosition().y))
                    poly = [fr.pt(a, b) for a, b in
                            polygons(pad, self.layer, outside=True)[0]]
                    self.points.setdefault(f"{eid}.land", []).append(
                        (x, y, poly))
            ports, kind = element_ports(fp, self.layer, self.w)
            for name, ((x, y), (dx, dy)) in ports.items():
                px, py = fr.pt(x, y)
                ddx, ddy = fr.vec(dx, dy)
                key = name if kind == "numbered" else compass(ddx, ddy)
                self.ports[f"{eid}.{key}"] = ((px, py), (ddx, ddy))
        # an element's lands, numbered in reading order: <id>.land1, ...
        for key in list(self.points):
            lands = sorted(self.points.pop(key), key=lambda t: (t[1], t[0]))
            for i, land in enumerate(lands, 1):
                self.points[f"{key}{i}"] = land

    def place_packages(self):
        for pid, p in self.c.get("packages", {}).items():
            fp = self.load(p["footprint"])
            (ex, ey), (dx, dy) = self.ports[p["port"]]
            pads = {q.GetNumber(): q for q in fp.Pads()}
            on, to = pads[p["pin"]], pads[p["toward"]]
            ox, oy = to_mm(on.GetPosition().x), to_mm(on.GetPosition().y)
            vx = to_mm(to.GetPosition().x) - ox
            vy = to_mm(to.GetPosition().y) - oy
            # turn the package so `toward` lies straight on from `pin`
            rot = math.degrees(math.atan2(-dy, dx) - math.atan2(-vy, vx))
            fr0 = Frame((0, 0), rot, False)
            lead = p.get("lead", self.lead)
            tx, ty = ex + dx * lead, ey + dy * lead
            rx, ry = fr0.vec(ox, oy)
            fr = Frame((tx - rx, ty - ry), rot, False)
            first = len(self.line)
            self.lengths.append((f"{p['port']} to {pid}.{p['pin']}",
                                 self.segment((ex, ey), (tx, ty), self.w)))
            # the lead's run and its joint on the land are the land's; the
            # joint at the port stays with the element it leaves
            self.tags[first] = self.tags[first + 2] = f"{pid}.{p['pin']}"
            for num, q in pads.items():
                # the land's own outline, turned with the package: a
                # rectangle at any angle is a polygon, not a size
                x, y = fr.pt(to_mm(q.GetPosition().x),
                             to_mm(q.GetPosition().y))
                poly = [fr.pt(a, b) for a, b in
                        polygons(q, pcbnew.F_Cu, outside=True)[0]]
                self.points[f"{pid}.{num}"] = (x, y, poly)
            for item in fp.GraphicalItems():
                if item.GetLayer() != pcbnew.F_Fab or \
                        item.GetClass() != "PCB_SHAPE" or \
                        item.GetShape() != pcbnew.SHAPE_T_SEGMENT:
                    continue
                a = fr.pt(to_mm(item.GetStart().x), to_mm(item.GetStart().y))
                b = fr.pt(to_mm(item.GetEnd().x), to_mm(item.GetEnd().y))
                seg = pcbnew.PCB_SHAPE(self.fp, pcbnew.SHAPE_T_SEGMENT)
                seg.SetStart(pcbnew.VECTOR2I(MM(a[0]), MM(a[1])))
                seg.SetEnd(pcbnew.VECTOR2I(MM(b[0]), MM(b[1])))
                seg.SetLayer(pcbnew.F_Fab)
                seg.SetWidth(MM(0.1))
                self.fp.Add(seg)

    # joins ----------------------------------------------------------------

    def join(self, j):
        (p1, d1), (p2, d2) = self.ports[j["from"]], self.ports[j["to"]]
        a = (p1[0] + d1[0] * self.lead, p1[1] + d1[1] * self.lead)
        b = (p2[0] + d2[0] * self.lead, p2[1] + d2[1] * self.lead)
        # one corner: leave along the first port's axis
        corner = (a[0], b[1]) if abs(d1[1]) > abs(d1[0]) else (b[0], a[1])
        pts = [p1, a, corner, b, p2]
        extra = j.get("extra", 0.0)
        if extra:
            pts = self.bend(pts, extra, j.get("side", 1))
        self.lengths.append((f"{j['from']} to {j['to']}",
                             self.path(pts, self.w)))

    def bend(self, pts, extra, side):
        """Lengthen a path by `extra` with a U bend in its longest run."""
        i = max(range(len(pts) - 1),
                key=lambda k: math.dist(pts[k], pts[k + 1]))
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        L = math.dist(pts[i], pts[i + 1])
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        nx, ny = -uy * side, ux * side
        h, half = extra / 2.0, self.w
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        u1 = (mx - ux * half, my - uy * half)
        u2 = (mx + ux * half, my + uy * half)
        return pts[:i + 1] + [u1, (u1[0] + nx * h, u1[1] + ny * h),
                              (u2[0] + nx * h, u2[1] + ny * h), u2] + \
            pts[i + 1:]

    def stub(self, s):
        """A line off a port's lead to a land: from the middle of the lead,
        `length` along `dir`. With a via in the land it is a shorted stub."""
        (x, y), (dx, dy) = self.ports[s["from"]]
        d = {"N": (0, -1), "S": (0, 1), "E": (1, 0), "W": (-1, 0)}[s["dir"]]
        start = (x + dx * self.lead / 2, y + dy * self.lead / 2)
        end = (start[0] + d[0] * s["length"], start[1] + d[1] * s["length"])
        self.lengths.append((f"stub off {s['from']}",
                             self.segment(start, end, s["width"])))
        land = s.get("land", 0.6)
        # the land reaches OVERLAP back over the stub's end: copper that
        # only abuts is not joined for KiCad's net tie
        long_ = land + OVERLAP
        cx = end[0] + d[0] * (land - OVERLAP) / 2
        cy = end[1] + d[1] * (land - OVERLAP) / 2
        sx, sy = (long_, land) if d[0] else (land, long_)
        self.rect_pad(s["pad"], cx, cy, sx, sy)

    # pads -----------------------------------------------------------------

    def pads(self):
        for number, points in self.c["pads"].items():
            for point in [points] if isinstance(points, str) else points:
                self.pad(number, point)

    def pad(self, number, point):
        if point in self.points:
            self.poly_pad(self.points[point][2], number, mask=True)
            return
        (x, y), (dx, dy) = self.ports[point]
        # a land of line width at the port's end, a lead long, reaching
        # OVERLAP back over the element so the two are joined, not abutted
        long_ = self.lead + OVERLAP
        cx = x + dx * (self.lead - OVERLAP) / 2
        cy = y + dy * (self.lead - OVERLAP) / 2
        sx, sy = (long_, self.w) if abs(dx) > abs(dy) \
            else (self.w, long_)
        self.rect_pad(number, cx, cy, sx, sy)

    def build(self):
        self.place_elements()
        print("ports:")
        for k, ((x, y), (dx, dy)) in sorted(self.ports.items()):
            print(f"  {k:12s} at {x:7.3f} {y:7.3f}  out {dx:5.2f} {dy:5.2f}")
        self.place_packages()
        for j in self.c.get("joins", []):
            self.join(j)
        for s in self.c.get("stubs", []):
            self.stub(s)
        self.pads()
        self.line_copper()
        if self.c.get("net_tie"):
            self.fp.AddNetTiePadGroup(self.c["net_tie"])
        self.fp.SetLibDescription(self.c.get("description", ""))
        self.fp.Value().SetText(self.c["name"])
        self.fp.Reference().SetText("REF**")
        self.fp.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)
        print("lengths:")
        for name, L in self.lengths:
            print(f"  {name:28s} {L:7.3f} mm")
        pcbnew.FootprintSave(self.lib, self.fp)
        print(f"wrote {self.lib}/{self.c['name']}.kicad_mod")


if __name__ == "__main__":
    design, comp = sys.argv[1], sys.argv[2]
    Composer(design, json.loads(Path(comp).read_text())).build()
