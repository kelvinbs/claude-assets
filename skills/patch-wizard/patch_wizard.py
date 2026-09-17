"""Aperture-fed patch, as a KiCad footprint wizard.

One radiator, built as the copper actually is:

  the patch      a pad on an outer layer
  the aperture   a rule area on the ground layer, copper pour not allowed,
                 so the ground pour is cut where the slot belongs. An
                 aperture is an absence of copper; a pad would be copper
  the feed       a pad on the feed layer, crossing under the slot

Every dimension is a parameter. The defaults are one openEMS run,
tools/rf-simulation/px1, taken whole. A number in a simulation is not in
force - the record owns what is built.

The wizard appears in the footprint editor under RF, "Aperture-fed patch".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class PatchWizard(FootprintWizardBase.FootprintWizard):

    # px1, taken whole
    PX1 = {
        "patch_w": 10.45, "patch_l": 8.18,
        "ap_w": 4.0, "ap_l": 0.4,
        "feed_w": 0.2377, "feed_stub": 4.008,
        "patch_layer": "F.Cu", "gnd_layer": "In1.Cu", "feed_layer": "In2.Cu",
    }

    def GetName(self):
        return "Aperture-fed patch"

    def GetDescription(self):
        return ("One aperture-fed patch: radiator, the slot as a cut in the "
                "ground pour, and the feed stub across it")

    def GenerateParameterList(self):
        p = self.PX1
        self.AddParam("Patch", "width", self.uMM, p["patch_w"])
        self.AddParam("Patch", "length", self.uMM, p["patch_l"])
        self.AddParam("Patch", "layer", self.uString, p["patch_layer"])

        self.AddParam("Aperture", "width", self.uMM, p["ap_w"])
        self.AddParam("Aperture", "length", self.uMM, p["ap_l"])
        self.AddParam("Aperture", "ground layer", self.uString, p["gnd_layer"])
        self.AddParam("Aperture", "rotated", self.uBool, False)

        self.AddParam("Feed", "line width", self.uMM, p["feed_w"])
        self.AddParam("Feed", "stub past slot", self.uMM, p["feed_stub"])
        self.AddParam("Feed", "layer", self.uString, p["feed_layer"])
        self.AddParam("Feed", "draw", self.uBool, True)

    def CheckParameters(self):
        for group, key in (("Patch", "width"), ("Patch", "length"),
                           ("Aperture", "width"), ("Aperture", "length"),
                           ("Feed", "line width")):
            if self.parameters[group][key] <= 0:
                self.parameter_errors[group][key] = "must be positive"
        if self.parameters["Aperture"]["width"] > \
                self.parameters["Patch"]["width"]:
            self.parameter_errors["Aperture"]["width"] = \
                "slot is wider than the patch it feeds"

    def GetValue(self):
        p = self.parameters["Patch"]
        return "patch_%gx%g" % (pcbnew.ToMM(p["width"]),
                                pcbnew.ToMM(p["length"]))

    def _layer(self, name, fallback=pcbnew.F_Cu):
        n = self.board.GetLayerID(name) if self.board else -1
        return n if n >= 0 else fallback

    def _pad(self, number, w, h, layer, x=0, y=0):
        pad = pcbnew.PAD(self.module)
        pad.SetSize(pcbnew.VECTOR2I(int(w), int(h)))
        pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetLayerSet(pcbnew.LSET(self._layer(layer)))
        pad.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
        pad.SetNumber(str(number))
        return pad

    def _aperture(self, w, h, layer, x=0, y=0):
        """The slot: a rule area that forbids copper pour, so the ground
        pour is cut. Not a pad - a pad is copper."""
        z = pcbnew.ZONE(self.module)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowZoneFills(True)
        z.SetDoNotAllowTracks(False)
        z.SetDoNotAllowVias(True)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        z.SetLayer(self._layer(layer, pcbnew.In1_Cu))
        pts = pcbnew.wxPoint_Vector() if hasattr(pcbnew, "wxPoint_Vector") \
            else None
        outline = pcbnew.SHAPE_POLY_SET()
        outline.NewOutline()
        for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2),
                       (w / 2, h / 2), (-w / 2, h / 2)):
            outline.Append(int(x + dx), int(y + dy))
        z.SetOutline(outline)
        return z

    def BuildThisFootprint(self):
        patch = self.parameters["Patch"]
        ap = self.parameters["Aperture"]
        feed = self.parameters["Feed"]

        self.module.Add(self._pad(1, patch["width"], patch["length"],
                                  patch["layer"]))

        ap_w, ap_l = ap["width"], ap["length"]
        if ap["rotated"]:
            ap_w, ap_l = ap_l, ap_w
        self.module.Add(self._aperture(ap_w, ap_l, ap["ground layer"]))

        if feed["draw"]:
            self.module.Add(self._pad(2, feed["line width"],
                                      feed["stub past slot"] * 2,
                                      feed["layer"]))

        self.draw.SetLayer(pcbnew.F_Fab)
        self.draw.Box(0, 0, patch["width"], patch["length"])
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box(0, 0, patch["width"] + pcbnew.FromMM(0.4),
                      patch["length"] + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


PatchWizard().register()
