"""T-junction splitter, as a KiCad footprint wizard.

Two quarter-wave Z0*sqrt(2) arms from a common point: each transforms its
Z0 load to 2*Z0, and the two in parallel present Z0 to the input. Splits in
phase with no isolation resistor, where the loads are matched. The RF split
of a printed I/Q mixer; `mixer_wizard.py` draws the same junction inside
its footprint.

  input    pad 1, the common port
  out 1    pad 2, the end of one arm
  out 2    pad 3, the end of the other
  arms     unnumbered copper

Every dimension is a parameter. The arm width is given, not computed.

The wizard appears in the footprint editor under RF, "T-junction splitter".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class TSplitWizard(FootprintWizardBase.FootprintWizard):

    # vna1 Layer1 microstrip over Layer2: 50 ohm 0.95 mm solved, 70.7 ohm
    # 0.47 mm, 4.465 mm a quarter wave. A number here is not in force
    VNA1 = {"z0_w": 0.95, "arm_w": 0.47, "arm_q": 4.465, "port_len": 1.0,
            "layer": "F.Cu"}

    _SCRATCH = None

    def GetName(self):
        return "T-junction splitter"

    def GetDescription(self):
        return "Printed T-junction: two quarter-wave arms, in-phase split"

    def GenerateParameterList(self):
        p = self.VNA1
        self.AddParam("Line", "Z0 width", self.uMM, p["z0_w"])
        self.AddParam("Line", "port length", self.uMM, p["port_len"])
        self.AddParam("Line", "layer", self.uString, p["layer"])
        self.AddParam("Arms", "width", self.uMM, p["arm_w"])
        self.AddParam("Arms", "quarter wave", self.uMM, p["arm_q"])

    def CheckParameters(self):
        for group in ("Line", "Arms"):
            for key, value in self.parameters[group].items():
                if key != "layer" and value <= 0:
                    self.parameter_errors[group][key] = "must be positive"

    def GetValue(self):
        return "tsplit_%gmm" % pcbnew.ToMM(
            self.parameters["Arms"]["quarter wave"])

    def _layer(self, name, fallback=pcbnew.F_Cu):
        board = getattr(self, "board", None)
        if board is None:
            if TSplitWizard._SCRATCH is None:
                TSplitWizard._SCRATCH = pcbnew.BOARD()
            board = TSplitWizard._SCRATCH
        n = board.GetLayerID(name)
        return n if n >= 0 else fallback

    def _pad(self, number, name, w, h, x, y):
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(w), int(h)))
        pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        seq = pcbnew.LSEQ()
        seq.push_back(self._layer(self.lay))
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
        self.lay = line["layer"]
        w0, pl = line["Z0 width"], line["port length"]
        aw, q = arms["width"], arms["quarter wave"]

        # input from the left into the junction at the origin; the arms run
        # up and down, each ending in its output port turned right
        self.module.Add(self._pad(1, "input", pl, w0, -pl / 2.0, 0))
        for number, sgn in ((2, -1), (3, 1)):
            self.module.Add(self._pad("", "arm", aw, q + w0, 0,
                                      sgn * q / 2.0))
            self.module.Add(self._pad(number, "out%d" % (number - 1), pl, w0,
                                      pl / 2.0, sgn * q))

        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box(0, 0, 2 * pl + pcbnew.FromMM(0.4),
                      2 * q + w0 + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


TSplitWizard().register()
