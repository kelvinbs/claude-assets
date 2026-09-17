"""Aperture-fed patch, as a KiCad footprint wizard.

Four features, each named for what it is:

  patch      the radiator, a pad on the patch layer
  aperture   the coupling slot: a rule area on the ground layer, copper pour
             not allowed, so the pour is cut. An aperture is an absence of
             copper - a pad would be copper
  feed       the 50 Ohm line, a pad on the feed layer, its feed point at the
             patch edge
  stub       the open length of that line carried past the aperture. In px1
             the trunk runs 3.0 past each slot

A layer is named by what it carries. Nothing here says "inner layer": that
would only be right for a feature meant for every inner layer.

Every dimension is a parameter. The defaults are one openEMS run,
tools/rf-simulation/px1, taken whole. A number in a simulation is not in
force - the record owns what is built.

The wizard appears in the footprint editor under RF, "Aperture-fed patch".
KiCad finds it only in its own scripting path; see SKILL.md.
"""

import pcbnew
import FootprintWizardBase


class PatchWizard(FootprintWizardBase.FootprintWizard):

    # tools/rf-simulation/px1, taken whole
    PX1 = {
        "patch_w": 10.45, "patch_l": 8.18,
        "ap_w": 4.0, "ap_l": 0.4,
        "feed_w": 0.2377, "stub": 3.0,
        "patch_layer": "F.Cu", "ground_layer": "In1.Cu",
        "feed_layer": "In2.Cu", "z0": 50.0,
    }

    def GetName(self):
        return "Aperture-fed patch"

    def GetDescription(self):
        return ("Aperture-fed patch: the radiator, the slot cut in the "
                "ground pour, and the 50 Ohm feed with its stub")

    def GenerateParameterList(self):
        p = self.PX1
        self.AddParam("Patch", "width", self.uMM, p["patch_w"])
        self.AddParam("Patch", "length", self.uMM, p["patch_l"])
        self.AddParam("Patch", "patch layer", self.uString, p["patch_layer"])

        self.AddParam("Aperture", "width", self.uMM, p["ap_w"])
        self.AddParam("Aperture", "length", self.uMM, p["ap_l"])
        self.AddParam("Aperture", "ground layer", self.uString,
                      p["ground_layer"])
        self.AddParam("Aperture", "along the patch width", self.uBool, True)

        self.AddParam("Feed", "line impedance ohm", self.uFloat, p["z0"])
        self.AddParam("Feed", "line width", self.uMM, p["feed_w"])
        self.AddParam("Feed", "stub past aperture", self.uMM, p["stub"])
        self.AddParam("Feed", "feed layer", self.uString, p["feed_layer"])

    def CheckParameters(self):
        for group, key in (("Patch", "width"), ("Patch", "length"),
                           ("Aperture", "width"), ("Aperture", "length"),
                           ("Feed", "line width"),
                           ("Feed", "stub past aperture")):
            if self.parameters[group][key] <= 0:
                self.parameter_errors[group][key] = "must be positive"
        patch = self.parameters["Patch"]
        ap = self.parameters["Aperture"]
        along = ap["along the patch width"]
        span = patch["width"] if along else patch["length"]
        if ap["width"] > span:
            self.parameter_errors["Aperture"]["width"] = \
                "longer than the patch dimension it lies along"

    def GetValue(self):
        p = self.parameters["Patch"]
        return "patch_%gx%g" % (pcbnew.ToMM(p["width"]),
                                pcbnew.ToMM(p["length"]))

    def _layer(self, name, fallback=pcbnew.F_Cu):
        n = self.board.GetLayerID(name) if self.board else -1
        return n if n >= 0 else fallback

    def _pad(self, number, name, w, h, layer, x=0, y=0):
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

    def _aperture(self, w, h, layer, x=0, y=0):
        z = pcbnew.ZONE(self.module)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowZoneFills(True)
        z.SetDoNotAllowVias(True)
        z.SetDoNotAllowTracks(False)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowFootprints(False)
        z.SetLayer(self._layer(layer, pcbnew.In1_Cu))
        pts = pcbnew.VECTOR_VECTOR2I()
        for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2),
                       (w / 2, h / 2), (-w / 2, h / 2)):
            pts.push_back(pcbnew.VECTOR2I(int(x + dx), int(y + dy)))
        z.AddPolygon(pts)
        return z

    def BuildThisFootprint(self):
        patch = self.parameters["Patch"]
        ap = self.parameters["Aperture"]
        feed = self.parameters["Feed"]

        pw, pl = patch["width"], patch["length"]

        # the patch
        self.module.Add(self._pad(1, "patch", pw, pl, patch["patch layer"]))

        # the aperture, lying along the patch width unless told otherwise
        if ap["along the patch width"]:
            ap_w, ap_l = ap["width"], ap["length"]
        else:
            ap_w, ap_l = ap["length"], ap["width"]
        self.module.Add(self._aperture(ap_w, ap_l, ap["ground layer"]))

        # the feed: 50 Ohm line, feed point at the patch edge, running in
        # under the aperture and on past it by the stub
        # from the patch edge at -pl/2, in under the aperture at 0, and on
        # past it by the stub
        run = pl / 2.0 + feed["stub past aperture"]
        centre = -pl / 2.0 + run / 2.0
        self.module.Add(self._pad(2, "feed", feed["line width"], run,
                                  feed["feed layer"], 0, centre))

        # fabrication: the patch edge, and the feed's impedance in words
        # the feed point, marked where it is: at the patch edge
        fy = -pl / 2.0
        self.draw.SetLayer(pcbnew.Dwgs_User)
        self.draw.SetLineThickness(pcbnew.FromMM(0.15))
        self.draw.Line(pcbnew.FromMM(-0.6), fy, pcbnew.FromMM(0.6), fy)
        self.draw.Line(0, fy - pcbnew.FromMM(0.6), 0, fy + pcbnew.FromMM(0.6))
        self.draw.TextSize(pcbnew.FromMM(0.7))
        self.draw.Text(pcbnew.FromMM(1.2), fy,
                       "feed point, %g ohm, %s"
                       % (feed["line impedance ohm"], feed["feed layer"]))
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Line(pcbnew.FromMM(-0.5), fy, pcbnew.FromMM(0.5), fy)
        self.draw.Line(0, fy, 0, fy - pcbnew.FromMM(0.8))

        self.draw.SetLayer(pcbnew.F_Fab)
        self.draw.Box(0, 0, pw, pl)
        self.draw.SetLineThickness(pcbnew.FromMM(0.1))
        self.draw.TextSize(pcbnew.FromMM(0.8))
        self.draw.Text(0, pl / 2.0 + pcbnew.FromMM(1.2),
                       "feed %g ohm, feed point at the patch edge"
                       % self.parameters["Feed"]["line impedance ohm"])

        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        self.draw.Box(0, 0, pw + pcbnew.FromMM(0.4), pl + pcbnew.FromMM(0.4))

        self.module.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM)


PatchWizard().register()
