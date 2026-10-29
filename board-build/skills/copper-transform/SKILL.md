---
name: copper-transform
description: Select board items — footprints by ref; tracks, vias, zones, rule areas by net, name or box — and translate, rotate, or mirror them about a horizontal or vertical line, nets kept; dry run first. Headless, on a saved .kicad_pcb, KiCad closed. Stage 6.
---

# copper-transform

Moves a group of board items as one body on a saved board file, headless.
KiCad must not be running: save all in KiCad and quit it first, then
confirm no `kicad` process remains — as for `kicad-update`.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — positions of footprints, tracks, vias, zones, rule areas; nets kept<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/copper-transform/copper-transform.py <board.kicad_pcb> [--ref PAT ...] [--net PAT ...] [--name PAT ...] [--box X0 Y0 X1 Y1] [--kind K ...] (--move DX DY | --rotate DEG --about X Y | --mirror-x X | --mirror-y Y) [--dry-run]
```

## 1 — The selection

**T1 — What each criterion names**

| # | Criterion | Applies to | Matches |
|---|---|---|---|
| 1.1 | `--kind` | — | limits to `footprint`, `track`, `via`, `zone`, `rule-area`; default all |
| 1.2 | `--ref PAT ...` | footprints | the reference |
| 1.3 | `--net PAT ...` | tracks, vias, zones | the net name |
| 1.4 | `--name PAT ...` | zones, rule areas | the zone name |
| 1.5 | `--box X0 Y0 X1 Y1` | every kind | the item's bounding box wholly inside; a footprint's without text |

- An item is selected when every criterion that applies to its kind
  matches, and at least one does. `--ref U13 --net '/RF/*'` is U13 and the
  RF copper; add `--box` to cut both to a region.
- Patterns are shell wildcards. An empty selection is refused.
- Run `--dry-run` first: every selected item, where it is, where it goes.

## 2 — The transform

**T2 — What each transform does**

| # | Transform | Does |
|---|---|---|
| 2.1 | `--move DX DY` | translates by `(DX, DY)` mm |
| 2.2 | `--rotate DEG --about X Y` | turns `DEG` counter-clockwise on screen about `(X, Y)` |
| 2.3 | `--mirror-x X` | mirrors about the vertical line x = X |
| 2.4 | `--mirror-y Y` | mirrors about the horizontal line y = Y |
| 2.5 | A footprint, mirrored | its position mirrored and its rotation, 180 − θ or −θ; its pads not. A footprint does not mirror on its own side, so a track into an asymmetric footprint no longer meets its pads. The notes name every such footprint |
| 2.6 | Zone fills | move with their zones and are not recomputed. Refill in KiCad, B |
| 2.7 | Footprint text | a footprint's text is kept readable by KiCad, so a turn and its inverse may leave a text angle of 180 for 0 |

- The write harness is `board-read` T3.

## 3 — What it refuses

- KiCad running, `board-read` T3 row 3.1
- No criterion, or a selection that holds nothing
- `--rotate` without `--about`
- A worker that fails or dies, a saved file that is cut short or that kicad-cli cannot load: the board is unchanged
