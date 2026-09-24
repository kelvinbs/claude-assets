---
name: cooperative-placement
description: Cooperative parts placement — gather one block's footprints above the board's upper right corner, packed and selected, for the User to place. Stage 6.
---

# cooperative-placement

Cooperative parts placement. The User places the board one block at a
time; Claude gathers each block for the User. The skill of stage 6, beside
`kicad-update`.

| Reads | Writes |
|---|---|
| `board.db` — `ref_table`<br>the board open in KiCad, live | the board open in KiCad — footprint positions and the selection. Nothing saved |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/cooperative-placement/cooperative-placement.py <board-dir> <room|ref> [--except REF ...] [--gap MM] [--width MM]
```

## 1 — The mode

- "Stage" and "cooperative placement" are the same thing. "Stage the
  driver", "stage PA A except U10" are orders to run this skill.
- An order is carried out, not discussed. No question, no proposal, no
  confirmation, no notes. The User's global rules on proposing and asking
  first do not apply to an order in this mode.
- "Except" names parts to leave where they are: `--except`.
- Only a refusal of the script, section 5, is reported, in one line.

**T1 — One turn of the mode**

| # | Who | Does |
|---|---|---|
| 1 | User | Names a block: a room, a schematic block, or a part; and any part to leave out |
| 2 | Claude | Runs the script with that name and `--except`. Nothing else |
| 3 | Claude | Replies with the script's one line. No notes, no comments |
| 4 | User | Drags the selected parts into place, and names the next block |

- KiCad stays open. The PCB is opened from the project window, so the
  schematic and the board stay linked. Claude never asks the User to close
  or reopen it.
- A KiCad that is not running is started by Claude, on the project:
  `open -F -a KiCad <project>.kicad_pro`. `-F` skips the macOS
  restore-windows prompt.
- Claude does no board work in this mode beyond this script. No script is
  written at runtime, §1.4 of `board-build-tool.md`.

## 2 — The block

**T2 — What the name selects**

| # | Name | Block |
|---|---|---|
| 1 | a room, `ref_table.name` | the room and everything under it by `parent` |
| 2 | a part, `ref_table.ref` | the part and everything under it by `parent` |
| 3 | a name held by nested rooms | the outermost; the inner ones are inside it |
| 4 | a name held by unrelated rooms | refused, naming each one's parent; name a part instead |

- Only parts on the open board are gathered: `ref_table.board` against the
  `-board-<board>` of the file's name. A part the record holds and the
  board does not is named at the end of the line.

## 3 — The pack

**T3 — Where the parts go**

| # | Item | Value |
|---|---|---|
| 1 | Size of a part | bounding box without text |
| 2 | Order | tallest first |
| 3 | Rows | no wider than `--width`, default 16 mm |
| 4 | Gap | `--gap` between parts on both axes, default 0.5 mm |
| 5 | Anchor | the cluster's lower right corner `--gap` above the upper right corner of Edge.Cuts |
| 6 | Finish | the parts selected in the editor |
| 7 | Left out | refs named by `--except`, not moved, not selected |

## 4 — The link to KiCad

- KiCad's IPC API, through `kicad-python` in the tool's venv, `.venv` at
  the plugin root. The script makes it on first run and runs itself in it;
  kept out of git.
- The API server must be on in KiCad's preferences.
- No commit is opened. KiCad 10.0.5 routes `BeginCommit` to the schematic
  editor's handler when both editors are open, and crashes in
  `API_HANDLER_EDITOR::checkForBusy()`. Undo therefore takes each part
  back on its own.
- `UpdateItems` crashes KiCad 10.0.5 the same way, in the schematic
  editor's `handleUpdateItems`, seen 2026-09-24 with the Schematic Editor
  open beside the PCB Editor. With the PCB Editor alone it has run clean.
- So the script refuses while a Schematic Editor window is open, and
  touches nothing. A crash leaves autosave files, and KiCad asks on the
  next launch whether to restore them. The refusal is what keeps that
  dialog from appearing.
- Claude never crashes or kills KiCad to get past a refusal.

## 5 — What it refuses

- No `board.db` in the board folder
- A name that is neither a room nor a ref
- A name held by unrelated rooms, T2 row 4
- No board open in KiCad, or no Edge.Cuts on it
- A Schematic Editor window open, section 4
