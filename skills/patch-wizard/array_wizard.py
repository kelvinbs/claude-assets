"""An array of aperture-fed patches, as one KiCad footprint wizard.

The same radiator as patch_wizard, repeated on a grid, with the pitch a
parameter rather than a placement. One object to place; move the array by
moving it, change the spacing by editing the footprint.

Pad numbering runs row by row from the top left: patch n is pad 3n+1, its
slot 3n+2, its feed stub 3n+3.
"""

import pcbnew
import FootprintWizardBase


class PatchArrayWizard(FootprintWizardBase.FootprintWizard):

    def GetName(self):
        return "Aperture-fed patch array"

    def GetDescription(self):
        return "A grid of aperture-fed patches: radiators, slots, feed stubs"

    def GenerateParameterList(self):
        self.AddParam("Array", "rows", self.uInteger, 2)
        self.AddParam("Array", "columns", self.uInteger, 1)
        self.AddParam("Array", "row pitch", self.uMM, 22.32)
        self.AddParam("Array", "column pitch", self.uMM, 22.45)

        self.AddParam("Patch", "width", self.uMM, 10.45)
        self.AddParam("Patch", "length", self.uMM, 8.18)
        self.AddParam("Patch", "layer", self.uString, "F.Cu")

        self.AddParam("Aperture", "width", self.uMM, 4.0)
        self.AddParam("Aperture", "length", self.uMM, 0.4)
        self.AddParam("Aperture", "layer", self.uString, "In1.Cu")
        self.AddParam("Aperture", "rotated", self.uBool, False)

        self.AddParam("Feed", "line width", self.uMM, 0.2377)
        self.AddParam("Feed", "trunk length", self.uMM, 28.32)
        self.AddParam("Feed", "layer", self.uString, "In2.Cu")
        self.AddParam("Feed", "draw", self.uBool, True)

    def CheckParameters(self):
        a = self.parameters["Array"]
        if a["rows"] < 1:
            self.parameter_errors["Array"]["rows"] = "at least one"
        if a["columns"] < 1:
            self.parameter_errors["Array"]["columns"] = "at least one"
        p = self.parameters["Patch"]
        if a["rows"] > 1 and a["row pitch"] < p["length"]:
            self.parameter_errors["Array"]["row pitch"] = \
                "closer than the patches are long"
        if a["columns"] > 1 and a["column pitch"] < p["width"]:
            self.parameter_errors["Array"]["column pitch"] = \
                "closer than the patches are wide"

    def GetValue(self):
        a = self.parameters["Array"]
        return "patch_array_%dx%d" % (a["columns"], a["rows"])

    def _layer(self, name):
        n = self.board.GetLayerID(name) if self.board else -1
        return n if n >= 0 else pcbnew.F_Cu

    def _rect_pad(self, number, w, h, layer, x, y):
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(w), int(h)))
        pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetLayerSet(pcbnew.LSET(self._layer(layer)))
        pad.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
        pad.SetNumber(str(number))
        return pad

    def BuildThisFootprint(self):
        a = self.parameters["Array"]
        patch = self.parameters["Patch"]
        ap = self.parameters["Aperture"]
        feed = self.parameters["Feed"]

        rows, cols = int(a["rows"]), int(a["columns"])
        rp, cp = a["row pitch"], a["column pitch"]
        x0 = -cp * (cols - 1) / 2.0
        y0 = -rp * (rows - 1) / 2.0

        ap_w, ap_l = ap["width"], ap["length"]
        if ap["rotated"]:
            ap_w, ap_l = ap_l, ap_w

        n = 0
        for c in range(cols):
            x = x0 + c * cp
            for r in range(rows):
                y = y0 + r * rp
                self.module.Add(self._rect_pad(3 * n + 1, patch["width"],
                                               patch["length"],
                                               patch["layer"], x, y))
                self.module.Add(self._rect_pad(3 * n + 2, ap_w, ap_l,
                                               ap["layer"], x, y))
                n += 1
            # one trunk per column, running the length of the column
            if feed["draw"]:
                self.module.Add(self._rect_pad(3 * n, feed["line width"],
                                               feed["trunk length"],
                                               feed["layer"], x, 0))

        self.draw.SetLayer(pcbnew.F_Fab)
        for c in range(cols):
            x = x0 + c * cp
            for r in range(rows):
                y = y0 + r * rp
                self.draw.Box(x, y, patch["width"], patch["length"])
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box(0, 0,
                      cp * (cols - 1) + patch["width"] + pcbnew.FromMM(0.4),
                      rp * (rows - 1) + patch["length"] + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


PatchArrayWizard().register()
