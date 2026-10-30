---
name: zone-net-set
description: Put copper zones on another net, selected by present net, name, uuid, box or layer; tracks, vias and pads never change. Headless, on a saved .kicad_pcb, KiCad closed. Stage 6.
---

# zone-net-set

Reassigns the net of copper zones on a saved board file, headless. KiCad
must not be running.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — zone nets<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/zone-net-set/zone-net-set.py <board.kicad_pcb> --to NET [--net PAT ...] [--name PAT ...] [--uuid PREFIX ...] [--box X0 Y0 X1 Y1] [--layer L] [--dry-run]
```

- Copper zones only; rule areas, tracks, vias and pads are never touched.
- Every criterion given must match; at least one is required.
- Refused: no selection, nothing selected, a NET the board does not hold.
- Fills are not recomputed: `zone-fill`. Run `--dry-run` first. The write
  harness is `board-read` T3.
