"""Coupled-line directional coupler, as a KiCad footprint wizard.

Two lines run side by side for a quarter guide wavelength. A wave on the
main line couples backward onto the coupled line: it leaves the coupled
line at the end nearest the main line's input. A wave travelling the main
line the other way, a reflection, leaves at the other end. On a VNA port
the end nearest the port therefore samples the reflected wave, and the end
nearest the source the forward wave.

  input      pad 1, the main line's source end
  coupled    pad 2, the coupled line's end beside the input: forward sample
  ground     pads 3 and 4, plated holes to the ground plane
  isolated   pad 5, the coupled line's end beside the direct port:
             reflected sample
  direct     pad 6, the main line's far end, toward the port

The numbers match KiCad's RF:DC4759J5020AHF-2 coupler symbol, so a part
drawn with that symbol takes this footprint with no pin map.

The main line and the coupled line are unnumbered copper, as the arms of
`wilkinson_wizard.py` are: line, not a port. The coupled line's ports turn
away at right angles, so they stand clear of the main line's.

Every dimension is a parameter. Width and gap are given, not computed: the
even- and odd-mode impedances that set the coupling depend on the stack,
the same rule the Wilkinson and rat-race wizards state.

The wizard appears in the footprint editor under RF, "Directional coupler".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class CouplerWizard(FootprintWizardBase.FootprintWizard):

    # vna1 Layer1 microstrip over Layer2, h 0.5124 mm, Dk 4.3: 50 ohm is
    # 0.95 mm, lambda_g 17.4 mm at 9.4 GHz. The gap is a starting point
    # for about 20 dB, not a solved value: a number here is not in force
    VNA1 = {
        "quarter": 17.4 / 4.0,
        "main_w": 0.95,
        "cpl_w": 0.95,
        "gap": 0.5,
        "port_w": 0.95,
        "port_len": 1.0,
        # a printed part is drawn top-side; which face it ends on is a
        # layout decision, and KiCad's flip makes it
        "layer": "F.Cu",
        "via_drill": 0.3,
        "via_size": 0.6,
        "z0": 50.0,
    }

    def GetName(self):
        return "Directional coupler"

    def GetDescription(self):
        return ("Printed coupled-line coupler: a quarter-wave section, "
                "input, direct, coupled and isolated ports, two ground vias")

    def GenerateParameterList(self):
        p = self.VNA1
        self.AddParam("Line", "system impedance ohm", self.uFloat, p["z0"])
        self.AddParam("Line", "port width", self.uMM, p["port_w"])
        self.AddParam("Line", "port length", self.uMM, p["port_len"])
        self.AddParam("Line", "layer", self.uString, p["layer"])

        self.AddParam("Section", "length", self.uMM, p["quarter"])
        self.AddParam("Section", "main width", self.uMM, p["main_w"])
        self.AddParam("Section", "coupled width", self.uMM, p["cpl_w"])
        self.AddParam("Section", "gap", self.uMM, p["gap"])

        self.AddParam("Ground", "via drill", self.uMM, p["via_drill"])
        self.AddParam("Ground", "via size", self.uMM, p["via_size"])

    def CheckParameters(self):
        for group, key in (("Line", "port width"), ("Line", "port length"),
                           ("Section", "length"), ("Section", "main width"),
                           ("Section", "coupled width"), ("Section", "gap"),
                           ("Ground", "via drill"), ("Ground", "via size")):
            if self.parameters[group][key] <= 0:
                self.parameter_errors[group][key] = "must be positive"
        if self.parameters["Ground"]["via size"] <= \
                self.parameters["Ground"]["via drill"]:
            self.parameter_errors["Ground"]["via size"] = \
                "no annular ring round the drill"
        if 2 * self.parameters["Line"]["port width"] >= \
                self.parameters["Section"]["length"]:
            self.parameter_errors["Section"]["length"] = \
                "shorter than the two coupled ports side by side"

    def GetValue(self):
        return "coupler_%gmm_gap%gmm" % (
            pcbnew.ToMM(self.parameters["Section"]["length"]),
            pcbnew.ToMM(self.parameters["Section"]["gap"]))

    def _layer(self, name, fallback=pcbnew.F_Cu):
        n = self.board.GetLayerID(name) if self.board else -1
        return n if n >= 0 else fallback

    def _pad(self, number, name, w, h, layer, x, y):
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
        try:
            pad.SetPinFunction(name)
        except AttributeError:
            pass
        return pad

    def _via(self, number, drill, size, x, y):
        """A plated hole through every copper layer: the coupler's ground,
        where the symbol's GND pins land."""
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(size), int(size)))
        pad.SetDrillSize(pcbnew.VECTOR2I(int(drill), int(drill)))
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetLayerSet(pcbnew.LSET.AllCuMask())
        pad.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
        pad.SetNumber(str(number))
        try:
            pad.SetPinFunction("GND")
        except AttributeError:
            pass
        return pad

    def BuildThisFootprint(self):
        line = self.parameters["Line"]
        sec = self.parameters["Section"]
        gnd = self.parameters["Ground"]

        L = sec["length"]
        mw = sec["main width"]
        cw = sec["coupled width"]
        gap = sec["gap"]
        pw = line["port width"]
        pl = line["port length"]
        lay = line["layer"]

        # main line on y = 0, coupled line below it, y down
        yc = mw / 2 + gap + cw / 2

        # the two lines, unnumbered: line, not ports
        self.module.Add(self._pad("", "main", L, mw, lay, 0, 0))
        self.module.Add(self._pad("", "coupled", L, cw, lay, 0, yc))

        # main line ports, straight on
        self.module.Add(self._pad(1, "input", pl, pw, lay,
                                  -L / 2 - pl / 2, 0))
        self.module.Add(self._pad(6, "direct", pl, pw, lay,
                                  L / 2 + pl / 2, 0))

        # coupled line ports, turned down and away from the main line
        ys = yc + cw / 2 + pl / 2
        self.module.Add(self._pad(2, "coupled", pw, pl, lay,
                                  -L / 2 + pw / 2, ys))
        self.module.Add(self._pad(5, "isolated", pw, pl, lay,
                                  L / 2 - pw / 2, ys))

        # ground: one hole above the main line, one between the coupled
        # ports, each a port width clear of the copper
        vs = gnd["via size"]
        vd = gnd["via drill"]
        self.module.Add(self._via(3, vd, vs, 0, ys))
        self.module.Add(self._via(4, vd, vs, 0, -mw / 2 - pw - vs / 2))

        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        top = -mw / 2 - pw - vs
        bottom = ys + pl / 2
        self.draw.Box(0, (top + bottom) / 2,
                      L + 2 * pl + pcbnew.FromMM(0.4),
                      bottom - top + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


CouplerWizard().register()
