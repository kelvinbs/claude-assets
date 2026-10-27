"""Branch-line hybrid, as a KiCad footprint wizard.

A square of four quarter-wave lines: the two series arms at Z0/sqrt(2), the
two shunt arms at Z0. Driven at one corner, the two far corners are 3 dB
and 90 degrees apart and the fourth corner is isolated. The LO split of a
printed I/Q mixer; `mixer_wizard.py` draws the same square inside its
footprint.

  input      pad 1, top left
  through    pad 2, top right: -90 degrees
  coupled    pad 3, bottom right: -180 degrees
  isolated   pad 4, bottom left: terminate in Z0
  arms       unnumbered copper, the device's own line

Every dimension is a parameter. Widths are given, not computed, the rule
the other wizards state. Corners are square, not mitred.

The wizard appears in the footprint editor under RF, "Branch-line hybrid".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class BranchLineWizard(FootprintWizardBase.FootprintWizard):

    # vna1 Layer1 microstrip over Layer2: 50 ohm 0.95 mm solved, 35.4 ohm
    # 1.62 mm an estimate. A number here is not in force
    VNA1 = {"z0_w": 0.95, "z0_q": 4.35, "z35_w": 1.62, "z35_q": 4.27,
            "port_len": 1.0, "layer": "F.Cu"}

    _SCRATCH = None

    def GetName(self):
        return "Branch-line hybrid"

    def GetDescription(self):
        return ("Printed branch-line: four quarter-wave arms, 3 dB and 90 "
                "degrees between the outputs")

    def GenerateParameterList(self):
        p = self.VNA1
        self.AddParam("Line", "Z0 width", self.uMM, p["z0_w"])
        self.AddParam("Line", "Z0 quarter wave", self.uMM, p["z0_q"])
        self.AddParam("Line", "port length", self.uMM, p["port_len"])
        self.AddParam("Line", "layer", self.uString, p["layer"])
        self.AddParam("Series", "Z0 over sqrt2 width", self.uMM, p["z35_w"])
        self.AddParam("Series", "Z0 over sqrt2 quarter wave", self.uMM,
                      p["z35_q"])

    def CheckParameters(self):
        for group in ("Line", "Series"):
            for key, value in self.parameters[group].items():
                if key != "layer" and value <= 0:
                    self.parameter_errors[group][key] = "must be positive"

    def GetValue(self):
        return "branchline_%gmm" % pcbnew.ToMM(
            self.parameters["Series"]["Z0 over sqrt2 quarter wave"])

    def _layer(self, name, fallback=pcbnew.F_Cu):
        board = getattr(self, "board", None)
        if board is None:
            if BranchLineWizard._SCRATCH is None:
                BranchLineWizard._SCRATCH = pcbnew.BOARD()
            board = BranchLineWizard._SCRATCH
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
        ser = self.parameters["Series"]
        self.lay = line["layer"]
        w0, q0, pl = line["Z0 width"], line["Z0 quarter wave"], \
            line["port length"]
        w35, q35 = ser["Z0 over sqrt2 width"], ser["Z0 over sqrt2 quarter wave"]
        hx, hy = q35 / 2.0, q0 / 2.0

        # series arms along x, shunt arms along y; each run carries half a
        # width more at each end so the corners close square
        for y in (-hy, hy):
            self.module.Add(self._pad("", "series", q35 + w0, w35, 0, y))
        for x in (-hx, hx):
            self.module.Add(self._pad("", "shunt", w0, q0 + w35, x, 0))

        for number, name, sx, sy in ((1, "input", -1, -1),
                                     (2, "through", 1, -1),
                                     (3, "coupled", 1, 1),
                                     (4, "isolated", -1, 1)):
            self.module.Add(self._pad(number, name, pl, w0,
                                      sx * (hx + pl / 2.0), sy * hy))

        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box(0, 0, 2 * (hx + pl) + pcbnew.FromMM(0.4),
                      q0 + w35 + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


BranchLineWizard().register()
