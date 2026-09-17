---
name: patch-wizard
description: Make a footprint for a printed feature no library holds — an aperture-fed patch, or an array of them. Stage 5.
---

# patch-wizard

A footprint for copper that is the design, not a purchased device. The skill covers printed features: a patch, an array guide and a
Wilkinson divider. Stage 5's
other skill, `copy-kicad-part`, borrows a footprint from the installed
libraries; no library holds your antenna, so for a `pcb-feature` there is
nothing to borrow and the stage cannot finish. This skill is how it finishes.

| Reads | Writes |
|---|---|
| the parameters, given in KiCad's footprint editor | `lib/<nickname>.pretty/<name>.kicad_mod`, through KiCad's Save As |

No script runs here from the session. The wizard runs inside KiCad.

## Where it lives

KiCad loads a footprint wizard only from its own scripting path. The source
stays in the repository and is linked there:

```
ln -s <repo>/tools/board-build/skills/patch-wizard \
      ~/Documents/KiCad/<version>/scripting/plugins/patch-wizard
```

KiCad reads it at start, or on Tools, External Plugins, Refresh in the
footprint editor.

## The two wizards

| Wizard | Makes |
|---|---|
| Aperture-fed patch | one element, four named features: the patch, the aperture, the 50 Ohm feed, and the stub the feed carries past the aperture |
| Patch array guide | where the patches go — one outline per element, its centre, the pitch and the array extent. A graphic, not copper |
| Wilkinson divider | two quarter-wave arms, three ports and the isolation resistor's pads. Run backwards it is a combiner; the copper is the same |

The array is deliberately not copper. Drawing it as copper would put a
second, stale copy of every patch on the board; the patches are their own
footprints, placed against the guide.

## Using it

Footprint editor, File, New Footprint from Wizard, RF. Set the parameters,
then Save As into the project library under the name the record carries in
`parts_table.footprint`.

## The parameters

| Group | Parameter | What it is |
|---|---|---|
| Patch | width, length | the radiator |
| Patch | patch layer | the layer the patch is on, named for what it carries |
| Aperture | width, length | the coupling slot |
| Aperture | ground layer | the ground the slot is cut in |
| Aperture | along the patch width | which patch dimension the slot lies along |
| Feed | line impedance ohm | 50, and the footprint says so on the fabrication layer |
| Feed | line width | the width that gives that impedance in this stack |
| Feed | stub past aperture | the open length carried past the slot |
| Feed | feed layer | the layer the feed runs on |
| Array | rows, columns | the grid |
| Array | row pitch, column pitch | the spacing |
| Element | patch width, patch length | the outline the guide draws per element |
| Guide | crosshair size, label elements | how the guide marks each centre |
| Line | system impedance ohm, port width, port length, layer | the ports the divider presents, and where they sit |
| Arms | length, width, separation | a quarter wavelength at the design frequency, at the width that makes Z0·√2 on this stack |
| Resistor | pad size, pad separation | the chip resistor bridging the branches, 2·Z0 |

A layer is named by what it carries — the patch layer, the ground the
aperture is cut in, the feed layer. Nothing is called an inner layer: that
would only be right for a feature meant for every inner layer.

The arm width is given, not computed. The width that makes Z0·√2 depends on
the stack, and on the px1 stack it lands near the fabricator's minimum track,
so the choice belongs to whoever knows the stack.

## What the features are

| Feature | How it is built | Why |
|---|---|---|
| patch | pad 1 on the patch layer | the radiator, and where the net lands |
| aperture | a rule area on the ground layer, copper pour not allowed | an aperture is the absence of copper. A pad would be copper and would cut nothing |
| feed | pad 2 on the feed layer | the 50 Ohm line, its feed point at the patch edge |
| stub | the part of pad 2 past the aperture | the open length that tunes the coupling |

The aperture only shows its effect on a board with a filled ground pour. In
the footprint editor there is nothing to cut, so it looks like an empty
rectangle.

## The defaults are not the record

They are the geometry of one openEMS run, `tools/rf-simulation/px1`. A number
in a simulation is not in force. What is built is what `docs/00`-`08` carries,
and where the document carries nothing the value does not exist yet.

## What it refuses

- A dimension at or below zero
- A slot wider than the patch it feeds
- A pitch closer than the patches are long or wide
