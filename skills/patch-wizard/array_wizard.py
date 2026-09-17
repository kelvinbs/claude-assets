"""An array of aperture-fed patches, as a placement graphic.

The array is not copper. It is the guide that says where the patches go: one
outline per element on the fabrication layer, the element pitch, and the
array's own extent. The patches themselves are separate footprints, placed
against this.

Nothing here is on a copper layer, so nothing here can short, pour or
connect. Drawing the array as copper would put a second, stale copy of every
patch on the board.

Every dimension is a parameter. The defaults are one openEMS run,
tools/rf-simulation/px1, taken whole.

The wizard appears in the footprint editor under RF, "Patch array guide".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class PatchArrayGuideWizard(FootprintWizardBase.FootprintWizard):

    # tools/rf-simulation/px1, taken whole
    PX1 = {
        "rows": 2, "columns": 1,
        "row_pitch": 22.32, "column_pitch": 22.45,
        "patch_w": 10.45, "patch_l": 8.18,
    }

    def GetName(self):
        return "Patch array guide"

    def GetDescription(self):
        return ("Where the patches go: one outline per element, the pitch, "
                "and the array extent. A graphic, not copper")

    def GenerateParameterList(self):
        p = self.PX1
        self.AddParam("Array", "rows", self.uInteger, p["rows"])
        self.AddParam("Array", "columns", self.uInteger, p["columns"])
        self.AddParam("Array", "row pitch", self.uMM, p["row_pitch"])
        self.AddParam("Array", "column pitch", self.uMM, p["column_pitch"])

        self.AddParam("Element", "patch width", self.uMM, p["patch_w"])
        self.AddParam("Element", "patch length", self.uMM, p["patch_l"])

        self.AddParam("Guide", "crosshair size", self.uMM, 1.0)
        self.AddParam("Guide", "label elements", self.uBool, True)

    def CheckParameters(self):
        a = self.parameters["Array"]
        e = self.parameters["Element"]
        if a["rows"] < 1:
            self.parameter_errors["Array"]["rows"] = "at least one"
        if a["columns"] < 1:
            self.parameter_errors["Array"]["columns"] = "at least one"
        if a["rows"] > 1 and a["row pitch"] < e["patch length"]:
            self.parameter_errors["Array"]["row pitch"] = \
                "closer than the patches are long"
        if a["columns"] > 1 and a["column pitch"] < e["patch width"]:
            self.parameter_errors["Array"]["column pitch"] = \
                "closer than the patches are wide"

    def GetValue(self):
        a = self.parameters["Array"]
        return "patch_array_guide_%dx%d" % (int(a["columns"]), int(a["rows"]))

    def BuildThisFootprint(self):
        a = self.parameters["Array"]
        e = self.parameters["Element"]
        g = self.parameters["Guide"]

        rows, cols = int(a["rows"]), int(a["columns"])
        rp, cp = a["row pitch"], a["column pitch"]
        pw, pl = e["patch width"], e["patch length"]
        x0 = -cp * (cols - 1) / 2.0
        y0 = -rp * (rows - 1) / 2.0
        half = g["crosshair size"] / 2.0

        self.draw.SetLayer(pcbnew.F_Fab)
        self.draw.SetLineThickness(pcbnew.FromMM(0.1))

        n = 0
        for c in range(cols):
            x = x0 + c * cp
            for r in range(rows):
                y = y0 + r * rp
                # where the patch sits, and its centre
                self.draw.Box(x, y, pw, pl)
                self.draw.Line(x - half, y, x + half, y)
                self.draw.Line(x, y - half, x, y + half)
                n += 1
                if g["label elements"]:
                    self.draw.TextSize(pcbnew.FromMM(0.8))
                    self.draw.Text(x, y + pl / 2.0 + pcbnew.FromMM(0.9),
                                   "AE%d" % n)

        # the array's extent, and the pitch it was drawn at
        self.draw.SetLayer(pcbnew.Cmts_User)
        self.draw.SetLineThickness(pcbnew.FromMM(0.15))
        self.draw.Box(0, 0, cp * (cols - 1) + pw, rp * (rows - 1) + pl)
        self.draw.TextSize(pcbnew.FromMM(1.0))
        self.draw.Text(0, rp * (rows - 1) / 2.0 + pl / 2.0 + pcbnew.FromMM(2.5),
                       "patch guide %dx%d, row pitch %g, column pitch %g"
                       % (cols, rows, pcbnew.ToMM(rp), pcbnew.ToMM(cp)))

        self.module.SetAttributes(pcbnew.FP_EXCLUDE_FROM_POS_FILES
                                  | pcbnew.FP_EXCLUDE_FROM_BOM)


PatchArrayGuideWizard().register()
