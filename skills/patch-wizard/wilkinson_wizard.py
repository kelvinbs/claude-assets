"""Wilkinson divider, as a KiCad footprint wizard.

Three printed features on the RF page are the same structure: the LO tap,
the TX splitter and the RX combiner. A Wilkinson is two quarter-wave arms
of Z0*sqrt(2) from a common port, bridged at their far ends by a resistor
of 2*Z0. Run backwards it is a combiner; nothing about the copper changes.

  common     pad 1, the single port
  arms       two lengths of line, each a quarter wavelength at the design
             frequency, drawn as copper on the feed layer
  branches   pads 2 and 3, the two ports
  resistor   two pads bridging the branches, taking a chip resistor

Every dimension is a parameter. The arm width is given, not computed: the
width that makes Z0*sqrt(2) depends on the stack, and on the px1 stack it
lands near the fabricator's minimum track, so the choice belongs to whoever
knows the stack rather than to this script.

The wizard appears in the footprint editor under RF, "Wilkinson divider".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class WilkinsonWizard(FootprintWizardBase.FootprintWizard):

    # tools/rf-simulation/px1: lambda_g 15.3801 at 9.4 GHz, 50 ohm
    # stripline 0.2377 wide on In2.Cu
    PX1 = {
        "arm_len": 15.3801 / 4.0,
        "arm_w": 0.12,
        "port_w": 0.2377,
        "gap": 1.2,
        "port_len": 1.0,
        "layer": "In2.Cu",
        "res_pad": 0.6,
        "res_len": 1.0,
        "z0": 50.0,
    }

    def GetName(self):
        return "Wilkinson divider"

    def GetDescription(self):
        return ("Printed Wilkinson: two quarter-wave arms, three ports and "
                "the isolation resistor's pads")

    def GenerateParameterList(self):
        p = self.PX1
        self.AddParam("Line", "system impedance ohm", self.uFloat, p["z0"])
        self.AddParam("Line", "port width", self.uMM, p["port_w"])
        self.AddParam("Line", "port length", self.uMM, p["port_len"])
        self.AddParam("Line", "layer", self.uString, p["layer"])

        self.AddParam("Arms", "length", self.uMM, p["arm_len"])
        self.AddParam("Arms", "width", self.uMM, p["arm_w"])
        self.AddParam("Arms", "separation", self.uMM, p["gap"])

        self.AddParam("Resistor", "pad size", self.uMM, p["res_pad"])
        self.AddParam("Resistor", "pad separation", self.uMM, p["res_len"])

    def CheckParameters(self):
        for group, key in (("Line", "port width"), ("Line", "port length"),
                           ("Arms", "length"), ("Arms", "width"),
                           ("Arms", "separation"), ("Resistor", "pad size")):
            if self.parameters[group][key] <= 0:
                self.parameter_errors[group][key] = "must be positive"
        if self.parameters["Arms"]["separation"] < \
                self.parameters["Resistor"]["pad separation"]:
            self.parameter_errors["Arms"]["separation"] = \
                "closer than the resistor's own pads"

    def GetValue(self):
        return "wilkinson_%gmm" % pcbnew.ToMM(self.parameters["Arms"]["length"])

    def _layer(self, name, fallback=pcbnew.F_Cu):
        n = self.board.GetLayerID(name) if self.board else -1
        return n if n >= 0 else fallback

    def _pad(self, number, name, w, h, layer, x, y):
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

    def BuildThisFootprint(self):
        line = self.parameters["Line"]
        arms = self.parameters["Arms"]
        res = self.parameters["Resistor"]

        L = arms["length"]
        aw = arms["width"]
        gap = arms["separation"]
        pw = line["port width"]
        pl = line["port length"]
        lay = line["layer"]

        # the common port, left
        self.module.Add(self._pad(1, "common", pl, pw, lay, -L / 2 - pl / 2, 0))
        # the two arms, drawn as copper
        for sign in (-1, 1):
            self.module.Add(self._pad(1, "common", L, aw, lay, 0,
                                      sign * gap / 2))
        # the branch ports, right
        self.module.Add(self._pad(2, "branch1", pl, pw, lay,
                                  L / 2 + pl / 2, -gap / 2))
        self.module.Add(self._pad(3, "branch2", pl, pw, lay,
                                  L / 2 + pl / 2, gap / 2))
        # the isolation resistor, bridging the arms at their far ends
        rp = res["pad size"]
        rs = res["pad separation"]
        self.module.Add(self._pad(2, "branch1", rp, rp, lay,
                                  L / 2 - rp, -rs / 2))
        self.module.Add(self._pad(3, "branch2", rp, rp, lay,
                                  L / 2 - rp, rs / 2))

        self.draw.SetLayer(pcbnew.Dwgs_User)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.TextSize(pcbnew.FromMM(0.7))
        self.draw.Text(0, gap / 2 + pcbnew.FromMM(1.6),
                       "Wilkinson, arms %g mm, isolation resistor %g ohm"
                       % (pcbnew.ToMM(L), 2 * line["system impedance ohm"]))

        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.Box(0, 0, L + 2 * pl + pcbnew.FromMM(0.4),
                      gap + aw + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


WilkinsonWizard().register()
