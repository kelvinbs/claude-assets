---
name: rule-area-copy
description: Create a rule area copying every setting of an existing named one, with a new name and an outline — a rectangle, a polygon, or the outline of refs, pads and tracks grown by N mm, union, convex or rectilinear. Headless, on a saved .kicad_pcb, KiCad closed. Stage 6.
---

# rule-area-copy

Adds a rule area with the settings of one already on the board, on a saved
board file, headless. KiCad must not be running: save all in KiCad and
quit it first, then confirm no `kicad` process remains — as for
`kicad-update`.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — new rule areas<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/rule-area-copy/rule-area-copy.py <board.kicad_pcb> --from NAME [--uuid PREFIX] --name NEW (--rect X0 Y0 X1 Y1 | --poly X Y X Y X Y ... | --around ITEM ... --grow MM [--hull union|convex|rect]) [--dry-run]
```

## 1 — The source

- Every setting of the rule area named `NAME` is copied: layers, keepouts,
  priority, placement source. Only the name and the outline are new; the
  tool checks the copy's settings against the source's.
- Several rule areas named `NAME` are one source when their settings
  agree. When they differ it is refused; `--uuid` then picks one by the
  uuid prefix `board-read rule-areas` prints.
- `NEW` is a name no zone or rule area on the board holds.

## 2 — The outline

**T2 — The outline forms**

| # | Form | Outline |
|---|---|---|
| 2.1 | `--rect X0 Y0 X1 Y1` | the rectangle between two corners |
| 2.2 | `--poly X Y X Y X Y ...` | the polygon through three or more points, in order |
| 2.3 | `--around ITEM ...` | the items' outline grown by `--grow` mm, default 0 |
| 2.4 | item `REF` | the footprint's courtyard on its side; its pads when it has none |
| 2.5 | item `REF.PAD` | the pad's shape |
| 2.6 | item `net:PAT` | every track and via of the nets the pattern names |
| 2.7 | `--hull union`, default | the union of the items' own shapes, each grown, round corners. Holes dropped |
| 2.8 | `--hull convex` | the convex hull of that union: one outline |
| 2.9 | `--hull rect` | the union of each item's bounding box, grown: edges on the axes only |

- KiCad holds one outline per zone. A union of pieces that do not touch
  becomes one rule area per piece, all named `NEW`, one table row each.
- Run `--dry-run` first. The write harness is `board-read` T3.

## 3 — What it refuses

- KiCad running, `board-read` T3 row 3.1
- No rule area named `NAME`; several that differ and no `--uuid`; a `--uuid` that does not pick one
- `NEW` already held
- A rectangle or outline with no area; `--poly` with fewer than three points or an odd count; a negative `--grow`
- An item not on the board; a net pattern with no track or via
- A worker that fails or dies, a saved file that is cut short or that kicad-cli cannot load: the board is unchanged
