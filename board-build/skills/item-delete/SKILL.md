---
name: item-delete
description: Remove tracks, vias, zones or rule areas from a saved .kicad_pcb by net, name, uuid, box or layer; footprints never. Headless, KiCad closed. Stage 6.
---

# item-delete

Removes board items on a saved board file, headless. KiCad must not be
running: save all in KiCad and quit it first.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — items removed<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/item-delete/item-delete.py <board.kicad_pcb> --kind K ... [--net PAT ...] [--name PAT ...] [--uuid PREFIX ...] [--box X0 Y0 X1 Y1] [--layer L] [--dry-run]
```

## 1 — The selection

| # | Criterion | Applies to |
|---|---|---|
| 1.1 | `--kind`, required | `track`, `via`, `zone`, `rule-area` |
| 1.2 | `--net` | tracks, vias, zones |
| 1.3 | `--name` | zones, rule areas |
| 1.4 | `--uuid` | zones, rule areas, by the prefix `board-read` prints |
| 1.5 | `--box` | every kind, bounding box wholly inside |
| 1.6 | `--layer` | tracks |

- Every criterion that applies must match, and at least one must apply.
- Items are removed with `BOARD.Delete`, never `BOARD.Remove`: a removed
  item left to Python crashes the garbage collector, radar_2
  crash-incidents 1.7.
- Run `--dry-run` first. The write harness is `board-read` T3.

## 2 — What it refuses

- KiCad running; no selection criterion; a selection that holds nothing
- A worker that fails or dies, a saved file cut short or that kicad-cli cannot load: the board is unchanged
