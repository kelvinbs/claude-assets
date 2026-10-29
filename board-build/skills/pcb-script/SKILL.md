---
name: pcb-script
description: Run a pcbnew script outside KiCad without the exit crash and its macOS dialog.
---

# pcb-script

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
