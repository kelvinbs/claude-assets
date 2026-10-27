"""Printed I/Q mixer, as a KiCad footprint wizard.

A drop-in for a packaged I/Q mixer to baseband: LO and RF in, I and Q out.
The copper is the device; four Schottky diodes, in two pairs, and one
termination resistor are soldered to it.

  LO split    a branch-line hybrid: LO in at one corner, the two outputs
              90 degrees apart, the fourth corner terminated in Z0
  RF split    a T-junction of two quarter-wave Z0*sqrt(2) arms: in phase
  mixers      two rat-race rings, one per channel. LO into sigma, RF into
              delta, a diode at each of the two outputs, opposite polarity:
              a single-balanced mixer
  IF          taken at the junction of each diode pair, outside the copper;
              an IF land per channel carries it to the pin

  pad 1   LO        pad 5   I ring, output A: diode land
  pad 2   RF        pad 6   I ring, output B: diode land
  pad 3   IF_I      pad 7   Q ring, output A: diode land
  pad 4   IF_Q      pad 8   Q ring, output B: diode land
                    pad 9   branch-line isolated port: termination land

DC: the rings, the hybrid and the RF split are one piece of copper, held at
ground through the termination. LO and RF must arrive DC-blocked.

The rings follow `ratrace_wizard.py`: six quarter waves round, ports 60
degrees apart. Here sigma faces the hybrid, so the I ring's ports run
delta 60, A 120, sigma 180, B 240 degrees, and the Q ring is its mirror.

Every dimension is a parameter. Widths are given, not computed, the rule
the other wizards state. Corners are square, not mitred.

The wizard appears in the footprint editor under RF, "I/Q mixer".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import math

import pcbnew
import FootprintWizardBase


class IQMixerWizard(FootprintWizardBase.FootprintWizard):

    # vna1 Layer1 microstrip over Layer2, h 0.5124 mm, Dk 4.3, 9.4 GHz.
    # 50 ohm 0.95 mm solved; 70.7 ohm 0.47 mm and 35.4 ohm 1.62 mm are
    # closed-form estimates. A number here is not in force
    VNA1 = {
        "z0_w": 0.95, "z0_q": 4.35,
        "z70_w": 0.47, "z70_q": 4.465,
        "z35_w": 1.62, "z35_q": 4.27,
        "port_len": 1.0, "land": 0.6, "gap": 1.5,
        # a printed part is drawn top-side; which face it ends on is a
        # layout decision, and KiCad's flip makes it
        "layer": "F.Cu",
    }

    _SCRATCH = None

    def GetName(self):
        return "I/Q mixer"

    def GetDescription(self):
        return ("Printed I/Q mixer: branch-line LO split, T-junction RF "
                "split, two rat-race single-balanced mixers, diode lands")

    def GenerateParameterList(self):
        p = self.VNA1
        self.AddParam("Line", "Z0 width", self.uMM, p["z0_w"])
        self.AddParam("Line", "Z0 quarter wave", self.uMM, p["z0_q"])
        self.AddParam("Line", "port length", self.uMM, p["port_len"])
        self.AddParam("Line", "layer", self.uString, p["layer"])
        self.AddParam("Ring", "Z0 sqrt2 width", self.uMM, p["z70_w"])
        self.AddParam("Ring", "Z0 sqrt2 quarter wave", self.uMM, p["z70_q"])
        self.AddParam("Hybrid", "Z0 over sqrt2 width", self.uMM, p["z35_w"])
        self.AddParam("Hybrid", "Z0 over sqrt2 quarter wave", self.uMM,
                      p["z35_q"])
        self.AddParam("Layout", "land size", self.uMM, p["land"])
        self.AddParam("Layout", "ring gap", self.uMM, p["gap"])

    def CheckParameters(self):
        for group in ("Line", "Ring", "Hybrid", "Layout"):
            for key, value in self.parameters[group].items():
                if key != "layer" and value <= 0:
                    self.parameter_errors[group][key] = "must be positive"

    def GetValue(self):
        return "iq_mixer_%gmm" % pcbnew.ToMM(
            self.parameters["Ring"]["Z0 sqrt2 quarter wave"])

    def _layer(self, name, fallback=pcbnew.F_Cu):
        """Outside the editor there is no board; a scratch one still knows
        the standard names, as in `ratrace_wizard.py`."""
        board = getattr(self, "board", None)
        if board is None:
            if IQMixerWizard._SCRATCH is None:
                IQMixerWizard._SCRATCH = pcbnew.BOARD()
            board = IQMixerWizard._SCRATCH
        n = board.GetLayerID(name)
        return n if n >= 0 else fallback

    def _pad(self, number, name, w, h, x, y, rot=0.0):
        """`number` empty makes copper that is not a port: KiCad joins it to
        whatever it touches and asks nothing of the router."""
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(w), int(h)))
        pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        seq = pcbnew.LSEQ()
        seq.push_back(self._layer(self.lay))
        pad.SetLayerSet(pcbnew.LSET(seq))
        pad.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
        pad.SetNumber(str(number))
        if rot:
            pad.SetOrientation(pcbnew.EDA_ANGLE(rot, pcbnew.DEGREES_T))
        try:
            pad.SetPinFunction(name)
        except AttributeError:
            pass
        return pad

    def _seg(self, x1, y1, x2, y2, w, name="line"):
        """A straight run of line, horizontal or vertical, as unnumbered
        copper. Each end carries half a width more, so two runs meeting at
        a right angle overlap into a square corner."""
        if abs(y1 - y2) < abs(x1 - x2):
            self.module.Add(self._pad("", name, abs(x2 - x1) + w, w,
                                      (x1 + x2) / 2, y1))
        else:
            self.module.Add(self._pad("", name, w, abs(y2 - y1) + w,
                                      x1, (y1 + y2) / 2))

    @staticmethod
    def _on(cx, cy, r, deg):
        """A point at `deg` round a circle; KiCad's y runs down."""
        a = math.radians(deg)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def _ring(self, cx, cy, r, width):
        """One unnumbered custom pad of four arcs, as the rat-race wizard
        draws its ring."""
        lay = self._layer(self.lay)
        ax, ay = self._on(cx, cy, r, 90.0)
        pad = pcbnew.PAD(self.module)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetShape(pcbnew.PAD_SHAPE_CUSTOM)
        pad.SetAnchorPadShape(lay, pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.VECTOR2I(int(width), int(width)))
        seq = pcbnew.LSEQ()
        seq.push_back(lay)
        pad.SetLayerSet(pcbnew.LSET(seq))
        pad.SetPosition(pcbnew.VECTOR2I(int(ax), int(ay)))
        pad.SetNumber("")
        for start in (0.0, 90.0, 180.0, 270.0):
            arc = pcbnew.PCB_SHAPE(None, pcbnew.SHAPE_T_ARC)
            pts = []
            for deg in (start, start + 45.0, start + 90.0):
                x, y = self._on(cx, cy, r, deg)
                pts.append(pcbnew.VECTOR2I(int(x - ax), int(y - ay)))
            arc.SetArcGeometry(pts[0], pts[1], pts[2])
            arc.SetWidth(int(width))
            arc.SetFilled(False)
            # the pad takes the shape; see `ratrace_wizard.py`
            arc.thisown = 0
            pad.AddPrimitiveShape(lay, arc)
        self.module.Add(pad)

    def _stub(self, number, name, cx, cy, r, deg, pl, w):
        """A port: a radial stub of line off the ring. Returns its outer
        end. Numbered stubs are lands; unnumbered ones meet a line."""
        x, y = self._on(cx, cy, r + pl / 2.0, deg)
        self.module.Add(self._pad(number, name, pl, w, x, y, rot=deg))
        return self._on(cx, cy, r + pl, deg)

    def BuildThisFootprint(self):
        line = self.parameters["Line"]
        ring = self.parameters["Ring"]
        hyb = self.parameters["Hybrid"]
        lay_p = self.parameters["Layout"]

        self.lay = line["layer"]
        w0, q0, pl = line["Z0 width"], line["Z0 quarter wave"], \
            line["port length"]
        w70, q70 = ring["Z0 sqrt2 width"], ring["Z0 sqrt2 quarter wave"]
        w35, q35 = hyb["Z0 over sqrt2 width"], hyb["Z0 over sqrt2 quarter wave"]
        land, gap = lay_p["land size"], lay_p["ring gap"]

        # branch-line hybrid, centred on the origin: series arms along x,
        # shunt arms along y
        hx, hy = q35 / 2.0, q0 / 2.0
        self._seg(-hx, -hy, hx, -hy, w35, "hybrid")
        self._seg(-hx, hy, hx, hy, w35, "hybrid")
        self._seg(-hx, -hy, -hx, hy, w0, "hybrid")
        self._seg(hx, -hy, hx, hy, w0, "hybrid")
        # LO in, top left; isolated port, bottom left, to its termination
        self.module.Add(self._pad(1, "LO", pl, w0, -hx - pl / 2.0, -hy))
        self.module.Add(self._pad(9, "TERM", pl, w0, -hx - pl / 2.0, hy))

        # the rings, one each side of the axis, sigma facing the hybrid
        r = 3.0 * q70 / math.pi
        yr = r + w70 / 2.0 + gap / 2.0 + pl
        xr = hx + pl + r + pl + gap
        ends = {}
        for side, cy in (("I", -yr), ("Q", yr)):
            self._ring(xr, cy, r, w70)
            # the Q ring mirrors the I ring about the axis
            ang = {"delta": 60.0, "A": 120.0, "sigma": 180.0, "B": 240.0}
            if side == "Q":
                ang = {k: -v % 360.0 for k, v in ang.items()}
            first = 5 if side == "I" else 7
            ends[side, "sigma"] = self._stub("", "sigma", xr, cy, r,
                                             ang["sigma"], pl, w0)
            ends[side, "delta"] = self._stub("", "delta", xr, cy, r,
                                             ang["delta"], pl, w0)
            ends[side, "A"] = self._stub(first, side + "A", xr, cy, r,
                                         ang["A"], pl, w0)
            ends[side, "B"] = self._stub(first + 1, side + "B", xr, cy, r,
                                         ang["B"], pl, w0)

        # LO: hybrid outputs to the sigma ports. Out right, a jog, along
        x_jog = hx + pl / 2.0
        for side, sy in (("I", -hy), ("Q", hy)):
            sx, ty = ends[side, "sigma"]
            self._seg(hx, sy, x_jog, sy, w0, "LO")
            self._seg(x_jog, sy, x_jog, ty, w0, "LO")
            self._seg(x_jog, ty, sx, ty, w0, "LO")

        # RF: a T-junction on the axis, right of the rings. Quarter-wave
        # Z0*sqrt(2) arms up and down, then Z0 line to the delta ports
        xj = xr + r + w70 / 2.0 + gap + pl
        for side, sgn in (("I", -1.0), ("Q", 1.0)):
            dx, dy = ends[side, "delta"]
            self._seg(xj, 0.0, xj, sgn * q70, w70, "RF split")
            self._seg(xj, sgn * q70, xj, dy, w0, "RF")
            self._seg(xj, dy, dx, dy, w0, "RF")
        self.module.Add(self._pad(2, "RF", pl, w0, xj + pl / 2.0, 0.0))

        # IF lands, one per channel, beside the ring's output A land: the
        # diode pair's junction joins here
        for side, number, sgn in (("I", 3, -1.0), ("Q", 4, 1.0)):
            ax, ay = ends[side, "A"]
            self.module.Add(self._pad(number, "IF_" + side, land, land,
                                      ax - pl - land, ay))

        top = -(yr + r + pl + land)
        left = -hx - pl - land
        right = xj + pl + land
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box((left + right) / 2.0, 0, right - left, -2.0 * top)

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


IQMixerWizard().register()
