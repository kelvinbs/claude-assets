---
name: bga-fanout
description: Fan out a ball grid array on the open board in steps — group the signal nets by the part they serve, route each group on F.Cu from its pads with no ball via as far as F.Cu holds, put a via in every other connected ball, strap the power balls on F.Cu, then route each group's remaining signals from their ball vias on F.Cu or In2.Cu. Every trace heads toward its part and ends in a via outside the courtyard with a GND via toward the part. Stage 6.
---

# bga-fanout

Fans out one ball grid array on the board open in KiCad, in steps. The User
routes on from the end vias, and picks up the power and GND vias with plane
shapes.

| Reads | Writes |
|---|---|
| the board open in KiCad, live<br>`<board-dir>/fanout/<ref>.json` | the board open in KiCad: vias and tracks around the part. Saved only with `--save`<br>`<board-dir>/fanout/<ref>.json`, by Claude |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> groups
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> top    --group NAME [--save] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> vias   [--save] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> straps [--save] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> route  --group NAME [--save] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/bga-fanout/bga-fanout.py <board-dir> <ref> clear  [--save] [--dry-run]
```

## 1 — Steps

**T1 — The steps, in order**

| # | Step | By | What it does |
|---|---|---|---|
| 1 | `groups` | script | Lists each signal net, the parts at its other end and the side each part sits on |
| 2 | Grouping | Claude | Groups the signal nets by the part they serve, judged from step 1 and the record: a net through a resistor or to two parts goes with the part it serves. Gives each group a direction, the side its part sits on. Writes `fanout/<ref>.json`, T3, and shows the groups to the User |
| 3 | `top --group` | script | Per group: its signals on F.Cu from their pads, no via in the ball, as many as F.Cu holds. Reports the rest |
| 4 | `vias` | script | A via in every connected ball not already routed from its pad: signal, power, GND |
| 5 | `straps` | script | Adjacent balls of one power net joined on F.Cu where clearance allows. Orthogonal first, a diagonal only to join two groups; widened up to 0.5 mm. The part's straps are redrawn |
| 6 | `route --group` | script | Per group: its remaining signals from their ball vias, on F.Cu or In2.Cu, In4.Cu as escape. Reports PASS, every net routed, or FAIL |
| — | `clear` | script | Removes the part's whole fan-out: signal tracks, straps, ball vias, end and layer-change vias with their GND vias. To start over |

## 2 — Routing, steps 3 and 6

**T2 — Rules and guidance**

| # | Item | Rule |
|---|---|---|
| 1 | Direction | A trace heads only in its group's direction, never back. On a straight side, N, S, E or W, it may drift across the direction up to `--band`, 2.4 mm by default |
| 2 | End via | Every trace ends in a via outside the courtyard. Guidance, not a line: nearer costs less, so end vias sit close where they fit and further out where they must. Columns 0.8 mm apart, rows 0.8 mm apart, alternate columns offset 0.4 mm |
| 3 | GND via | One per end via, 0.40 mm from it toward the part |
| 4 | Layer | A trace runs on one layer from its pad or ball via to its end via. Escape: from a ball via on In4.Cu, out of the ball field only, then one layer-change via, with a GND via 0.40 mm toward the part, to F.Cu or In2.Cu. In4.Cu costs double per step and the change costs extra, so the plane is cut as little as it can be. A pair never escapes |
| 4a | Clean-up | Each run is drawn taut: corners dropped where a straight or one-bend octilinear path clears all other copper and still heads the group's way |
| 5 | Differential pairs | `<stem>_P` and `<stem>_N` both in the run are a pair: one layer; adjacent, nothing between, which in the ball field allows one ball row; end vias side by side. Both or neither |
| 6 | Negotiation | The run's nets share cells and end sites; an overlap costs more each pass. What still conflicts at the end is dropped and reported |
| 7 | Scope | A run touches only its own group's nets of that step: their earlier tracks, end vias and GND vias are removed first |
| 8 | Report | Each net routed: layer, end via, drift. Pairs. Nets not routed |

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
- `top`, `route`: no `fanout/<ref>.json`, no such group, a net with no signal ball on the part
- `route`: a ball of the group with neither a via nor a route
- The Schematic Editor still open after the tool saved and closed it
