---
name: zone-fill
description: Refill every zone of a saved .kicad_pcb headless through kicad-cli and save it, with a back-up and a DRC table before and after. KiCad closed. Stage 6.
---

# zone-fill

Refills zones, headless. KiCad must not be running.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved; `.kicad_pro` | `*.kicad_pcb` — zone fills<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/zone-fill/zone-fill.py <board.kicad_pcb> [--dry-run]
```

- The fill is kicad-cli's, `pcb drc --refill-zones --save-board`, on a
  copy beside copies of the project files. pcbnew's filler crashes
  outside KiCad, `board-read` §4.
- The board is replaced only when the copy ends whole and kicad-cli loads
  it again, and not when the board changed on disk during the run.
