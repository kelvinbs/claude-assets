---
name: track-cut
description: Remove the stretch of a net's straight tracks between two points on their line, keeping and trimming what lies outside. Headless, on a saved .kicad_pcb, KiCad closed. Stage 6.
---

# track-cut

Opens a gap in a routed line, for a part to go in series. KiCad must not
be running.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved | `*.kicad_pcb` — tracks cut<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/track-cut/track-cut.py <board.kicad_pcb> --net NET --from X Y --to X Y [--layer L] [--dry-run]
```

- Every straight track on the net whose both ends lie within 0.01 mm of
  the line through the two points is cut: the part between the points
  goes, the parts outside stay, trimmed. Arcs and vias are not touched.
- Nothing cut is refused. Run `--dry-run` first. The write harness is
  `board-read` T3.
