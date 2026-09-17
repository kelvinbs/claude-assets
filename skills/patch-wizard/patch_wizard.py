"""Aperture-fed patch, as a KiCad footprint wizard.

One radiator: the patch on an outer copper layer, the coupling slot cut in
the ground below it, and the stub of feed line that crosses the slot. Every
dimension is a parameter. Nothing here is a design value - the defaults are
a starting point and the record owns what is built.

The wizard appears in the footprint editor under "RF", named "Aperture-fed
patch". KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class PatchWizard(FootprintWizardBase.FootprintWizard):

    def GetName(self):
        return "Aperture-fed patch"

    def GetDescription(self):
        return "One aperture-fed patch: radiator, coupling slot, feed stub"

    def GenerateParameterList(self):
        self.AddParam("Patch", "width", self.uMM, 10.45)
        self.AddParam("Patch", "length", self.uMM, 8.18)
        self.AddParam("Patch", "layer", self.uString, "F.Cu")

        self.AddParam("Aperture", "width", self.uMM, 4.0)
        self.AddParam("Aperture", "length", self.uMM, 0.4)
        self.AddParam("Aperture", "layer", self.uString, "In1.Cu")
        self.AddParam("Aperture", "rotated", self.uBool, False)

        self.AddParam("Feed", "line width", self.uMM, 0.2377)
        self.AddParam("Feed", "stub past slot", self.uMM, 4.008)
        self.AddParam("Feed", "layer", self.uString, "In2.Cu")
        self.AddParam("Feed", "draw", self.uBool, True)

    def CheckParameters(self):
        for group, key in (("Patch", "width"), ("Patch", "length"),
                           ("Aperture", "width"), ("Aperture", "length"),
                           ("Feed", "line width")):
            if self.parameters[group][key] <= 0:
                self.parameter_errors[group][key] = "must be positive"
        ap_w = self.parameters["Aperture"]["width"]
        patch_w = self.parameters["Patch"]["width"]
        if ap_w > patch_w:
            self.parameter_errors["Aperture"]["width"] = \
                "slot is wider than the patch it feeds"

    def GetValue(self):
        p = self.parameters["Patch"]
        return "patch_%gx%g" % (pcbnew.ToMM(p["width"]),
                                pcbnew.ToMM(p["length"]))

    def _layer(self, name):
        n = self.board.GetLayerID(name) if self.board else -1
        return n if n >= 0 else pcbnew.F_Cu

    def _rect_pad(self, number, w, h, layer, net_name=""):
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(w), int(h)))
        pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetLayerSet(pcbnew.LSET(self._layer(layer)))
        pad.SetPosition(pcbnew.VECTOR2I(0, 0))
        pad.SetNumber(str(number))
        return pad

    def BuildThisFootprint(self):
        patch = self.parameters["Patch"]
        ap = self.parameters["Aperture"]
        feed = self.parameters["Feed"]

        # 1 - the radiator
        self.module.Add(self._rect_pad(1, patch["width"], patch["length"],
                                       patch["layer"]))

        # 2 - the coupling slot, a pad on the ground layer. The zone that
        # fills that layer is knocked back by it, which is what cuts the slot
        ap_w, ap_l = ap["width"], ap["length"]
        if ap["rotated"]:
            ap_w, ap_l = ap_l, ap_w
        self.module.Add(self._rect_pad(2, ap_w, ap_l, ap["layer"]))

        # 3 - the feed stub, crossing the slot and running on past it
        if feed["draw"]:
            self.module.Add(self._rect_pad(3, feed["line width"],
                                           feed["stub past slot"] * 2,
                                           feed["layer"]))

        # the outline on the fabrication layer, the patch's own edge
        self.draw.SetLayer(pcbnew.F_Fab)
        self.draw.Box(0, 0, patch["width"], patch["length"])
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box(0, 0, patch["width"] + pcbnew.FromMM(0.4),
                      patch["length"] + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


PatchWizard().register()
