---
name: board-read
description: Read a saved .kicad_pcb headless — footprints, a part's pads, nets, zones, rule areas, tracks and vias — as Markdown tables. Read-only. Stage 6.
---

# board-read

Reads a saved board file, headless, and prints one Markdown table. Never
writes. The board-build way to look at a board outside KiCad: no script
is written for it, `board-build-tool.md` §1.4.

| Reads | Writes |
|---|---|
| `*.kicad_pcb`, saved; the `.kicad_pro` beside it | Markdown on stdout |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/board-read/board-read.py <board.kicad_pcb> footprints [--ref PAT ...] [--box X0 Y0 X1 Y1]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/board-read/board-read.py <board.kicad_pcb> pads <ref>
python3 ${CLAUDE_PLUGIN_ROOT}/skills/board-read/board-read.py <board.kicad_pcb> nets [PAT ...]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/board-read/board-read.py <board.kicad_pcb> zones [--net PAT ...] [--name PAT ...]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/board-read/board-read.py <board.kicad_pcb> rule-areas [--name PAT ...]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/board-read/board-read.py <board.kicad_pcb> tracks [--net PAT ...] [--box X0 Y0 X1 Y1] [--layer L]
```

## 1 — The queries

**T1 — What each query prints**

| # | Query | Row | Columns |
|---|---|---|---|
| 1.1 | `footprints` | one footprint; `--ref` patterns, `--box` origin inside | ref, value, footprint, x, y, rotation, layer |
| 1.2 | `pads <ref>` | one pad of the footprint | pad, net, x, y, size, rotation, layers |
| 1.3 | `nets` | one net | net, pads — the count |
| 1.4 | `nets PAT ...` | one pad on a net the patterns name | net, pad `REF.PAD`, x, y |
| 1.5 | `zones` | one copper zone; `--net`, `--name` | name, net, layers, clearance, min width, fill — mode, pad connection, thermal gap, spoke, islands, filled — priority, bbox |
| 1.6 | `rule-areas` | one rule area; `--name` | name, layers, keepouts, priority, bbox, uuid prefix |
| 1.7 | `tracks` | one track, arc or via; `--net`, `--layer`, `--box` an end inside | kind, net, layer, start or at, end, width or diameter and drill |

## 2 — Conventions

- Millimetres, KiCad's axes, y down, degrees counter-clockwise on screen.
- Patterns are shell wildcards, case-sensitive: `'U1*'`, `'/RF/*'`.
- Layers print by the board's own names, `Layer1` where F.Cu was renamed;
  every tool takes either name.
- It reads the saved file. What an open editor holds unsaved is not seen:
  save in KiCad first.
- It runs in KiCad's Python through `pcb-script` and leaves by `os._exit`.
  KiCad may stay open: nothing is written.

## 3 — The library

`board-read.py` is also the library of the four writing skills —
`place-relative`, `route-pad`, `copper-transform`, `rule-area-copy` —
loaded by sibling path, `board-build-tool.md` §5.3. It holds the harness
every one of them runs under.

**T3 — The write harness**

| # | Step | Rule |
|---|---|---|
| 3.1 | Guard | `pgrep -i kicad`: any KiCad process and the tool refuses, board unchanged. Save all in KiCad and quit it first, as for `kicad-update` |
| 3.2 | Worker | the edit runs in KiCad's Python, in its own process, through `pcb-script`: `os._exit`, never the wx teardown |
| 3.3 | Save | the worker saves to a temporary file beside the board, `.<name>.board-edit-<pid>/`, through the s-expression plugin: `PCB_IO_MGR.FindPlugin(KICAD_SEXP).SaveBoard`. Never `pcbnew.SaveBoard`, §4 |
| 3.4 | Back-up | `<board>.bak-<YYYYmmdd-HHMMSS>`, a copy of the board, once the worker has exited 0 and before anything is written to the board |
| 3.5 | Verify | the temporary file ends whole; `kicad-cli pcb drc` loads it and checks it, beside a copy of the `.kicad_pro`, as it does a copy of the board before. A load failure refuses, board unchanged |
| 3.6 | Replace | `os.replace` of the temporary file over the board: atomic, same folder. Refused when the board changed on disk during the run |
| 3.7 | Report | the change table, then the DRC table: each violation type whose count changed, and the total. Zones are not refilled |
| 3.8 | Dry run | `--dry-run`: the change table only; no back-up, nothing written |

## 4 — The safe save

- `pcbnew.SaveBoard` segfaults outside KiCad in
  `SETTINGS_MANAGER::SaveProjectAs`: it saves the project file through a
  settings manager that does not exist outside KiCad. Seen in radar_2
  crash-incidents 1.6, where it left the User's board partly written.
- The plugin's `SaveBoard` writes the board alone, and the project file is
  never touched. Its output reloads to the same items as the input,
  in another order.
- The worker never calls pcbnew's DRC or zone filler: their thread pools
  crash outside KiCad. DRC is kicad-cli's; zones are refilled in KiCad, B.

## 5 — Testing a writing tool

- A test runs on a scratch copy, never on the User's board.
- A scratch copy may be edited while KiCad runs: `BOARD_BUILD_SCRATCH=1`,
  and the board under a temporary directory, `/private/tmp`, `/tmp`,
  `/private/var/folders`, with no `~<name>.kicad_pcb.lck` beside it. Not for
  a project board, which never sits there.

## 6 — What it refuses

- A path that is not a `.kicad_pcb`, or no file there
- A footprint that is not on the board, or a reference two footprints carry
- A layer the board does not have
