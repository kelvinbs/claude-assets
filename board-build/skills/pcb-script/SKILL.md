---
name: pcb-script
description: Run a model-building pcbnew script of a simulation study outside KiCad without the exit crash and its macOS dialog. Not for board work — board edits go through board-read, place-relative, route-pad, copper-transform, rule-area-copy.
---

# pcb-script

Runs a pcbnew script outside KiCad. Its use is the model-building scripts
of simulation studies: scripts that build a study's own model boards from
parameters, in the study's folder.

It is not for board work. A project board is read with `board-read` and
changed with `place-relative`, `route-pad`, `copper-transform` and
`rule-area-copy`, which run under it with a back-up, a safe save and a
check, `board-read` T3. No script is written to read or change a project
board, `board-build-tool.md` §1.4.

Every pcbnew script run outside KiCad goes through `pcb-script`, never
through KiCad's Python directly. pcbnew outside KiCad segfaults in its wx
teardown at a normal exit, and macOS shows a crash dialog for each run.

| Reads | Writes |
|---|---|
| what the script reads | what the script writes |

```
skills/pcb-script/pcb-script -c "<code>" [args ...]
skills/pcb-script/pcb-script <script.py> [args ...]
```

The arguments are those of `python3 -c` and `python3 <script>`. It runs with
KiCad's own Python, runs the script to its end, lets its files close, flushes
output, and leaves by `os._exit` with the script's exit code.

**T1 — Known crashes outside KiCad**

| # | Call | Crash | Instead |
|---|---|---|---|
| 1.1 | interpreter exit | wx teardown, segfault | `os._exit`, this tool |
| 1.2 | `pcbnew.SaveBoard` | `SETTINGS_MANAGER::SaveProjectAs`, segfault; crash-incidents 1.6 | `PCB_IO_MGR.FindPlugin(PCB_IO_MGR.KICAD_SEXP).SaveBoard(path, board)`, the board alone |
| 1.3 | pcbnew DRC, zone fill | thread pool, segfault in `CacheTriangulation` | `kicad-cli pcb drc`; refill in KiCad |
