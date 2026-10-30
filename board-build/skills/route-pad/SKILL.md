---
name: route-pad
description: Add a straight or two-segment orthogonal track between two pads on one layer, net from the pads, width from the net class or given. Headless, on a saved .kicad_pcb, KiCad closed. Stage 6.
---

# route-pad

Adds one track between two pads of one net on a saved board file,
headless. KiCad must not be running: save all in KiCad and quit it first,
then confirm no `kicad` process remains — as for `kicad-update`.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved; `.kicad_pro` — `net_settings` | `*.kicad_pcb` — one or two track segments<br>`<board>.bak-<timestamp>` |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/route-pad/route-pad.py <board.kicad_pcb> REF.PAD[@X,Y] REF.PAD[@X,Y] [--layer L] [--width MM] [--shape auto|straight|hv|vh] [--neck W L [--neck-at a|b]] [--dry-run]
```

## 1 — The track

**T1 — What is drawn**

| # | Item | Value |
|---|---|---|
| 1.1 | Net | the pads' own. Different nets, or a pad on none, refused |
| 1.2 | Layer | the copper layer both pads are on; `--layer` when they share more than one |
| 1.3 | Width | the track width of the net's class in the `.kicad_pro`: explicit assignment, then the first pattern that matches, then Default. `--width` overrides |
| 1.4 | `straight` | one segment, pad centre to pad centre |
| 1.5 | `hv` | horizontal from the first pad, then vertical |
| 1.6 | `vh` | vertical from the first pad, then horizontal |
| 1.7 | `auto`, default | `straight` when the pads share an x or a y, else `hv` |
| 1.8 | `REF.PAD@X,Y` | among the pads carrying the number, the one nearest (X, Y) |
| 1.9 | `--neck W L` | the last L mm into pad b, or the first out of pad a with `--neck-at a`, is a filled copper taper on the net from the track width to W |

- No clearance check is made by the tool. The DRC table after the write
  shows what the track crosses.
- Run `--dry-run` first. The write harness is `board-read` T3.

## 2 — What it refuses

- KiCad running, `board-read` T3 row 3.1
- A pad not on its footprint; a pad number two pads carry, without `@X,Y`
- A neck longer than its segment
- Pads on different nets, or a pad on no net
- No copper layer shared, a `--layer` not on both, several shared and no `--layer`
- Two pads with one centre
- A worker that fails or dies, a saved file that is cut short or that kicad-cli cannot load: the board is unchanged
