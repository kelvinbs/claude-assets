---
name: bga-fanout
description: Fan out a ball grid array on the open board — a via in every connected ball, every signal to the courtyard line on the signal layer, the rest through the escape layer with a GND via beside each layer change, power balls strapped. Stage 6.
---

# bga-fanout

Fans out one ball grid array on the board open in KiCad. The User routes
on from the courtyard line, and picks up the power and GND vias with plane
shapes.

| Reads | Writes |
|---|---|
| the board open in KiCad, live | the board open in KiCad — vias and tracks around the part. Saved only with `--save` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> [--save] [--dry-run]
```

## 1 — Geometry

**T1 — Defaults**

| # | Item | Value | Option |
|---|---|---|---|
| 1 | Via | 0.25 mm pad, 0.15 mm drill, through | `--via`, `--drill` |
| 2 | Signal trace | 0.11 mm | `--track` |
| 3 | Copper clearance | 0.10 mm | `--clearance` |
| 4 | Hole clearance | 0.20 mm | `--hole-clearance` |
| 5 | Hole to hole | 0.22 mm | `--hole-to-hole` |
| 6 | Signal layer | In2.Cu | `--signal-layer` |
| 7 | Escape layer | In4.Cu | `--escape-layer` |
| 8 | Fence | the courtyard, F.Courtyard | `--fence`, `--fence-max` |
| 9 | Power nets | `^/?\+?\d+V\d*$` | `--power` |
| 10 | Grid | 0.1 mm; bends 0°, 45°, 90° | — |

- The board setup must allow T1 rows 1–5; the tool does not change it.

## 2 — What it draws

**T2 — The fan-out**

| # | Item | Rule |
|---|---|---|
| 1 | Ball via | In pad, on every connected ball: signal, power, GND |
| 2 | Signal | Ends on the signal layer, on the fence line |
| 3 | Escape | A signal the signal layer cannot hold runs on the escape layer to a layer-change via inside the courtyard, then on the signal layer to the fence line |
| 4 | GND via | One per layer-change via. The minimum via spacing, centre to centre: 0.40 mm at the T1 defaults. On the normal to the nearest courtyard edge, on its inner side |
| 5 | Direction | Traces head out. A step inward costs extra; inward only where nothing else fits |
| 6 | Negotiation | Signals share grid cells on both layers; an overlap costs more each pass until none remain. The escape layer costs a little more, so it is used only as needed |
| 7 | Power | Adjacent balls of one net strapped on the signal layer, orthogonal first, a diagonal only to join two groups; widened up to 0.5 mm. No power trace on the escape layer |
| 8 | Re-run | The part's fan-out tracks on both layers, its ball vias and the GND vias around it are removed first |
| 9 | Calls | One read of the board, one remove, one create. KiCad's busy answer is retried |
| 10 | Schematic Editor | Saved and closed first when its window is on screen, as `block-outline` T2 row 7 |
| 11 | Report | Signals fanned out, layer changes, inward excursions over 0.3 mm, straps |

- The fence moves out by half a pitch only up to `--fence-max`.
- Check after: DRC errors at the part, `kicad-cli pcb drc`.

## 3 — What it refuses

- No board open in KiCad
- No footprint `<ref>` on the open board
- No courtyard on F.Courtyard and no `--fence`
- No GND ball
- No fan-out without overlaps with the fence up to `--fence-max`
- The Schematic Editor still open after the tool saved and closed it
