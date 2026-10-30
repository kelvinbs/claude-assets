---
name: via-add
description: Add through vias on a net at points, along a polyline, or as a stitching fence around named rule areas grown by N mm, skipping any via that would clash, crowd another, leave the board or enter a courtyard, keep-out or another net's zone. Headless, KiCad closed. Stage 6.
---

# via-add

Adds vias on a saved board file, headless. KiCad must not be running.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — vias<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/via-add/via-add.py <board.kicad_pcb> --net NET (--at X Y ... | --along X Y X Y ... [--closed] | --around NAME ... [--box X0 Y0 X1 Y1] --grow G) [--pitch P] [--diameter D --drill H] [--clearance C] [--spacing S] [--cross-zone-net PAT ...] [--in-courtyard REF ...] [--dry-run]
```

## 1 — Where

| # | Mode | Points |
|---|---|---|
| 1.1 | `--at` | one via per point |
| 1.2 | `--along` | every `--pitch` mm along the polyline; `--closed` back to the start |
| 1.3 | `--around` | the rule areas matching the names, inside `--box` when given, united, grown by `--grow`, every `--pitch` mm along each outline: a stitching fence |

## 2 — What is skipped

| # | Reason | Rule |
|---|---|---|
| 2.1 | off the board | within the radius + 0.3 mm of the edge |
| 2.2 | crowded | within `--spacing` of any via; default 0.8 × pitch |
| 2.3 | clash | within the radius + `--clearance` of a pad, track, via or footprint copper of another net or none, any copper layer |
| 2.4 | courtyard | inside a footprint courtyard, except `--in-courtyard` |
| 2.5 | keep-out | inside a rule area that keeps vias out |
| 2.6 | zone | inside a zone of another net, except `--cross-zone-net`: planes a refill clears |

- Zones are not refilled: `zone-fill`. Run `--dry-run` first. The write
  harness is `board-read` T3.
