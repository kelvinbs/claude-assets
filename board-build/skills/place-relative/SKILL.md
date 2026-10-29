---
name: place-relative
description: Place a footprint so one of its pads sits at an offset from another footprint's pad, turned to face a way or to a rotation; or move or turn a set of footprints rigidly. Headless, on a saved .kicad_pcb, KiCad closed. Stage 6.
---

# place-relative

Places footprints on a saved board file, headless. KiCad must not be
running: save all in KiCad and quit it first, then confirm no `kicad`
process remains — as for `kicad-update`.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — footprint positions and rotations<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/place-relative/place-relative.py <board.kicad_pcb> <ref> --pad P --to REF.PAD [--offset DX DY] [--face R|L|U|D | --rot DEG] [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/place-relative/place-relative.py <board.kicad_pcb> --refs REF ... --move DX DY [--dry-run]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/place-relative/place-relative.py <board.kicad_pcb> --refs REF ... --rotate DEG --about X Y [--dry-run]
```

## 1 — The modes

**T1 — What each mode does**

| # | Mode | Does |
|---|---|---|
| 1.1 | Pad | turns `<ref>`, then moves it so its pad `P` sits at pad `REF.PAD`'s centre plus `(DX, DY)` mm |
| 1.2 | `--face` | the turn that makes pad `P` face that way from the footprint's origin: `R` +x, `L` −x, `U` −y, up on screen, `D` +y. Measured on the board, so a back-side part turns right too |
| 1.3 | `--rot` | the rotation outright, degrees |
| 1.4 | neither | the rotation it has |
| 1.5 | Rigid, `--move` | every named footprint moves by `(DX, DY)` mm |
| 1.6 | Rigid, `--rotate` | every named footprint turns `DEG` counter-clockwise on screen about `(X, Y)`, as one body |

- Footprints only. Their tracks, vias and zones stay; `copper-transform`
  moves those with them.
- Run `--dry-run` first: the table of what would move, nothing written.
- The write harness — guard, worker, safe save, back-up, kicad-cli check,
  atomic replace, DRC table — is `board-read` T3.

## 2 — What it refuses

- KiCad running, `board-read` T3 row 3.1
- Neither pad mode nor `--refs`, or both
- `--refs` without exactly one of `--move`, `--rotate`; `--rotate` without `--about`
- A pad not on the footprint, a pad number two pads carry, `--to` on the footprint itself
- `--face` for a pad on the footprint's origin: give `--rot`
- A worker that fails or dies, a saved file that is cut short or that kicad-cli cannot load: the board is unchanged
