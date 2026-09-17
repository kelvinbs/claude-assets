---
name: patch-wizard
description: Make a footprint for a printed feature no library holds — an aperture-fed patch, or an array of them. Stage 5.
---

# patch-wizard

A footprint for copper that is the design, not a purchased device. Stage 5's
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
| Aperture-fed patch | one radiator: the patch, the coupling slot in the ground below it, the feed stub that crosses the slot |
| Aperture-fed patch array | the same radiator on a grid, with one feed trunk per column. Pitch is a parameter, not a placement |

## Using it

Footprint editor, File, New Footprint from Wizard, RF. Set the parameters,
then Save As into the project library under the name the record carries in
`parts_table.footprint`.

## The parameters

| Group | Parameter | What it is |
|---|---|---|
| Patch | width, length | the radiator |
| Patch | layer | the outer copper the patch sits on |
| Aperture | width, length | the coupling slot |
| Aperture | layer | the ground the slot is cut in |
| Aperture | rotated | swaps the slot's two dimensions |
| Feed | line width | the feed line |
| Feed | stub past slot, trunk length | how far the line runs past the slot; in the array, the whole trunk |
| Feed | layer | the layer the feed runs on |
| Feed | draw | off when the feed is routed by hand instead |
| Array | rows, columns | the grid |
| Array | row pitch, column pitch | the spacing |

## What the pads are

Each patch is three pads on three layers: the radiator, the slot, the feed
stub. The slot is a pad on the ground layer, so the zone that fills that
layer is knocked back by it and the slot is cut. In the array the numbering
runs row by row from the top left: patch n is pad 3n+1, its slot 3n+2, and
the column's trunk takes the next free number.

## The defaults are not the record

They are the geometry of one openEMS run, `tools/rf-simulation/px1`. A number
in a simulation is not in force. What is built is what `docs/00`-`08` carries,
and where the document carries nothing the value does not exist yet.

## What it refuses

- A dimension at or below zero
- A slot wider than the patch it feeds
- A pitch closer than the patches are long or wide
