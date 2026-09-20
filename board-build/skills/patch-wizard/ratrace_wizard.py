"""Rat-race hybrid ring, as a KiCad footprint wizard.

A Wilkinson splits in phase and no variant of it reverses phase. Where the
two halves of a pair must be driven in opposition — a mirrored element pair,
whose feeds face each other — the printed device is the rat-race.

A ring of 1.5 guide wavelengths, of line at Z0*sqrt(2), with four ports on
it a quarter wavelength apart, so the fourth section closing the ring is
three quarters:

  delta   pad 1, at 0. Drive it and pads 2 and 4 are 3 dB and 180 apart
  out A   pad 2, a quarter wavelength round
  sigma   pad 3, a half wavelength round. Drive it and pads 2 and 4 are in
          phase; terminate it in Z0 when the ring runs as a 180 splitter
  out B   pad 4, three quarters round
  ring    the line itself, one unnumbered custom pad of four arcs

The ports sit at 0, 60, 120 and 180 degrees of physical angle: a quarter
wavelength of line is a sixth of a ring that is one and a half wavelengths
round. All four therefore stand in one half of the circle.

Every dimension is a parameter. The ring width is given, not computed: the
width that makes Z0*sqrt(2) depends on the stack, the same rule
`wilkinson_wizard.py` states for its arms.

The wizard appears in the footprint editor under RF, "Rat-race hybrid ring".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import math

import pcbnew
import FootprintWizardBase


class RatRaceWizard(FootprintWizardBase.FootprintWizard):

    # tools/rf-simulation/px1: lambda_g 15.3801 at 9.4 GHz, 50 ohm
    # stripline 0.2377 wide on In2.Cu. The same stack the Wilkinson
    # wizard defaults to, and the same caution: a number in a simulation
    # is not in force
    PX1 = {
        "quarter": 15.3801 / 4.0,
        "ring_w": 0.12,
        "port_w": 0.2377,
        "port_len": 1.0,
        # a printed part is drawn top-side; which face it ends on is a
        # layout decision, and KiCad's flip makes it
        "layer": "F.Cu",
        "z0": 50.0,
    }

    # physical angle of each port, in degrees, measured from the +x axis.
    # A quarter wavelength is a sixth of the ring, so 60 degrees apart
    PORTS = (
        (1, "delta", 0.0),
        (2, "outA", 60.0),
        (3, "sigma", 120.0),
        (4, "outB", 180.0),
    )

    def GetName(self):
        return "Rat-race hybrid ring"

    def GetDescription(self):
        return ("Printed rat-race: a 1.5 wavelength ring, four ports, "
                "3 dB and 180 degrees from the delta port")

    def GenerateParameterList(self):
        p = self.PX1
        self.AddParam("Line", "system impedance ohm", self.uFloat, p["z0"])
        self.AddParam("Line", "port width", self.uMM, p["port_w"])
        self.AddParam("Line", "port length", self.uMM, p["port_len"])
        self.AddParam("Line", "layer", self.uString, p["layer"])

        self.AddParam("Ring", "quarter wavelength", self.uMM, p["quarter"])
        self.AddParam("Ring", "track width", self.uMM, p["ring_w"])

    def _radius(self):
        """Circumference is six quarter wavelengths, so r = 6q / 2pi."""
        return self.parameters["Ring"]["quarter wavelength"] * 3.0 / math.pi

    def CheckParameters(self):
        for group, key in (("Line", "port width"), ("Line", "port length"),
                           ("Ring", "quarter wavelength"),
                           ("Ring", "track width")):
            if self.parameters[group][key] <= 0:
                self.parameter_errors[group][key] = "must be positive"
                return
        # two ports a sixth of the ring apart must not touch
        pitch = 2.0 * math.pi * self._radius() / 6.0
        if self.parameters["Line"]["port width"] >= pitch:
            self.parameter_errors["Line"]["port width"] = \
                "wider than the gap between two ports"

    def GetValue(self):
        return "ratrace_%gmm" % pcbnew.ToMM(
            self.parameters["Ring"]["quarter wavelength"])

    _SCRATCH = None

    def _layer(self, name, fallback=pcbnew.F_Cu):
        """The editor's own board names the layers, so a stack whose layers
        were renamed resolves against it. Outside the editor there is no
        board, and a scratch one still knows the standard names."""
        board = getattr(self, "board", None)
        if board is None:
            if RatRaceWizard._SCRATCH is None:
                RatRaceWizard._SCRATCH = pcbnew.BOARD()
            board = RatRaceWizard._SCRATCH
        n = board.GetLayerID(name)
        return n if n >= 0 else fallback

    def _pad(self, number, name, w, h, layer, x, y, rot=0.0):
        """`number` empty makes copper that is not a port: KiCad joins it to
        whatever it touches and asks nothing of the router."""
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(w), int(h)))
        pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        seq = pcbnew.LSEQ()
        seq.push_back(self._layer(layer))
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

    @staticmethod
    def _on_ring(r, deg):
        """A point on the ring. KiCad's y runs down, so the sine is negated
        and the ports stand in the upper half of the drawing."""
        a = math.radians(deg)
        return r * math.cos(a), -r * math.sin(a)

    def _ring_pad(self, r, width, layer):
        """The ring itself: one unnumbered custom pad carrying four thick
        arcs, one per section — three of a quarter wavelength and the one
        of three quarters that closes it. Unnumbered for the reason the
        Wilkinson's arms are: it is the device's own line, not a port, and
        a number would put it on a net and ask the router for a trace."""
        lay = self._layer(layer)
        anchor_x, anchor_y = self._on_ring(r, 270.0)

        pad = pcbnew.PAD(self.module)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetShape(pcbnew.PAD_SHAPE_CUSTOM)
        # the anchor takes a layer: since KiCad 9 a pad's shape is per
        # layer, and only the layer this one is on is set
        pad.SetAnchorPadShape(lay, pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.VECTOR2I(int(width), int(width)))
        seq = pcbnew.LSEQ()
        seq.push_back(lay)
        pad.SetLayerSet(pcbnew.LSET(seq))
        pad.SetPosition(pcbnew.VECTOR2I(int(anchor_x), int(anchor_y)))
        pad.SetNumber("")
        try:
            pad.SetPinFunction("ring")
        except AttributeError:
            pass

        # primitives sit relative to the pad's own position
        for start_deg, end_deg in ((0.0, 60.0), (60.0, 120.0),
                                   (120.0, 180.0), (180.0, 360.0)):
            mid_deg = (start_deg + end_deg) / 2.0
            arc = pcbnew.PCB_SHAPE(None, pcbnew.SHAPE_T_ARC)
            pts = []
            for deg in (start_deg, mid_deg, end_deg):
                x, y = self._on_ring(r, deg)
                pts.append(pcbnew.VECTOR2I(int(x - anchor_x),
                                           int(y - anchor_y)))
            arc.SetArcGeometry(pts[0], pts[1], pts[2])
            arc.SetWidth(int(width))
            arc.SetFilled(False)
            # the pad takes the shape; without handing ownership over,
            # Python frees it and the next allocation writes over the
            # primitive, which comes back as whatever was drawn after
            arc.thisown = 0
            pad.AddPrimitiveShape(lay, arc)

        return pad

    def BuildThisFootprint(self):
        line = self.parameters["Line"]
        ring = self.parameters["Ring"]

        r = self._radius()
        rw = ring["track width"]
        pw = line["port width"]
        pl = line["port length"]
        lay = line["layer"]

        self.module.Add(self._ring_pad(r, rw, lay))

        # the four ports, each a stub of system-impedance line standing
        # radially off the ring
        for number, name, deg in self.PORTS:
            x, y = self._on_ring(r + pl / 2.0, deg)
            self.module.Add(self._pad(number, name, pl, pw, lay, x, y,
                                      rot=deg))

        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        span = 2.0 * (r + pl) + pcbnew.FromMM(0.4)
        self.draw.Box(0, 0, span, span)

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


RatRaceWizard().register()
