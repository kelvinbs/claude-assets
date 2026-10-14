---
name: bga-fanout
description: Fan out a ball grid array on the open board in steps — a via in every connected ball, power balls strapped on F.Cu, signal nets grouped by the part they serve, then one group at a time routed toward that part to end vias on the courtyard edge, each with a GND via toward the part. Stage 6.
---

# bga-fanout

Fans out one ball grid array on the board open in KiCad, in steps. The User
routes on from the end vias, and picks up the power and GND vias with plane
shapes.

| Reads | Writes |
|---|---|
| the board open in KiCad, live<br>`<board-dir>/fanout/<ref>.json` | the board open in KiCad — vias and tracks around the part. Saved only with `--save`<br>`<board-dir>/fanout/<ref>.json`, by Claude |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> vias   [--save] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> straps [--save] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> groups
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> route --group NAME [--band MM] [--save] [--dry-run]
```

## 1 — Steps

**T1 — The steps, in order**

| # | Step | By | What it does |
|---|---|---|---|
| 1 | `vias` | script | A via in every connected ball that has none: signal, power, GND |
| 2 | `straps` | script | Adjacent balls of one power net strapped on F.Cu where clearance allows, on no other layer. Orthogonal first, a diagonal only to join two groups; widened up to 0.5 mm. The part's straps are removed and redrawn |
| 3 | `groups` | script | Lists each signal net with the parts at its other end and the side each part sits on |
| 4 | Grouping | Claude | Groups the signal nets by the part they serve, judged from step 3 and the record: a net through a resistor or to two parts goes with the part it serves. Gives each group a direction, the side its part sits on. Writes `<board-dir>/fanout/<ref>.json`, T3, and shows the groups to the User |
| 5 | `route --group NAME` | script | Routes one group, T2. Run once per group |

## 2 — Routing a group

**T2 — The rules**

| # | Item | Rule |
|---|---|---|
| 1 | Layers | F.Cu or In2.Cu. A trace stays on one layer from its ball via to its end via |
| 2 | Direction | A trace heads only in its group's direction, never back. On a straight side, N, S, E or W, it may move across the direction, up to `--band` from its ball, 1.6 mm by default |
| 3 | End | Every trace ends in a via on the courtyard edge of the side it faces, or on a column 0.8 mm further out when the edge is full. Rows 0.8 mm apart, alternate columns offset 0.4 mm |
| 4 | GND via | One per end via, 0.40 mm from it toward the part, on the normal to the courtyard edge |
| 4a | Differential pairs | `<stem>_P` and `<stem>_N` both in the group are a pair. A pair takes one layer. Its two traces are adjacent: nothing between them — in the ball field one ball row at most, outside it the closest spacing. Its two end vias are neighbours on the same edge |
| 5 | Negotiation | The group's nets share cells and end vias; an overlap costs more each pass until none remain |
| 6 | Re-run | The group's own tracks on F.Cu and In2.Cu, its end vias and their GND vias are removed first. Nothing else is touched |
| 7 | Report | Each net: layer, end via, drift across the direction. Each pair and its layer |

**T3 — `fanout/<ref>.json`**

```json
{"groups": [{"name": "ADC", "dir": "W", "nets": ["/ADC_SDIO", "/ADC_SEN"]}]}
```

- `dir` is one of N, S, E, W, NE, NW, SE, SW.

**T4 — Defaults**

| # | Item | Value | Option |
|---|---|---|---|
| 1 | Via | 0.25 mm pad, 0.15 mm drill, through | `--via`, `--drill` |
| 2 | Signal trace | 0.11 mm | `--track` |
| 3 | Copper clearance | 0.10 mm | `--clearance` |
| 4 | Hole clearance | 0.20 mm | `--hole-clearance` |
| 5 | Hole to hole | 0.22 mm | `--hole-to-hole` |
| 6 | Power nets | `^/?\+?\d+V\d*$` | `--power` |
| 7 | Grid | 0.1 mm; bends 0°, 45°, 90° | — |

- The board setup must allow T4 rows 1–5; the tool does not change it.
- Check after: DRC errors at the part, `kicad-cli pcb drc`.
- The Schematic Editor is saved and closed first when its window is on screen, as `block-outline` T2 row 7. A board edit with it open crashes KiCad.

## 3 — What it refuses

- No board open in KiCad
- No footprint `<ref>` on the open board
- No courtyard on F.Courtyard
- No GND ball
- `route`: no `fanout/<ref>.json`, no such group, a net with no signal ball on the part, a ball with no via
- `route`: no conflict-free fan-out toward the direction within the band
- The Schematic Editor still open after the tool saved and closed it
