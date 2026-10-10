---
name: cooperative-placement
description: Cooperative parts placement — gather one block's footprints above the board's upper right corner, packed and selected, for the User to place; or place a scope's parts block by block along a flow from an LLM-written strategy. Stage 6.
---

# cooperative-placement

Cooperative parts placement. The User places the board one block at a
time; Claude gathers each block for the User. The skill of stage 6, beside
`kicad-update`.

| Reads | Writes |
|---|---|
| `board.db` — `ref_table`<br>the board open in KiCad, live<br>the strategy, §6 | the board open in KiCad — footprint positions, rotations and the selection. Nothing saved |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/cooperative-placement/cooperative-placement.py <board-dir> <room|ref> [--except REF ...] [--gap MM] [--width MM]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/cooperative-placement/cooperative-placement.py <board-dir> --flow <strategy.json> [--dry-run]
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
- No commit is opened.
- KiCad 10.0.5 keeps the schematic editor's API handler registered after
  the Schematic Editor window closes. Its frame is gone, and the next
  board call reaches it first and crashes KiCad in
  `API_HANDLER_EDITOR::checkForBusy()`: seen 2026-09-24 on `BeginCommit`
  and `UpdateItems`. A crash leaves the restore dialog on the next launch.
- So the Schematic Editor, once opened, stays open until KiCad quits.
  Open or never opened, the script runs clean. Claude never closes it.

## 5 — What it refuses

- No `board.db` in the board folder
- A name that is neither a room nor a ref
- A name held by unrelated rooms, T2 row 4
- No board open in KiCad, or no Edge.Cuts on it
- Flow, §6: a strategy that does not parse, or lacks `scope`, `anchor`,
  `direction` or `blocks`
- Flow: a block that repeats a part, a part of the scope no block holds,
  a block with no single parent part and no `parent` named
- Flow: a part of the scope not on the front
- Flow: a part or block with no clear place, T6.3

## 6 — Flow placement

Places a scope's parts on the board, block by block along a flow. The LLM
writes the strategy; the script does the geometry. Out-of-scope parts are
never moved.

**T6.1 — The process**

| # | Who | Does |
|---|---|---|
| 1 | User | Names a scope — a room or a part — and, when it is not plain, the flow: the primary current path, the signal flow, or another |
| 2 | Claude | Reads the scope from the record, its rooms, parents and nets, and the datasheets where a part's role is not plain |
| 3 | Claude | Writes the strategy, T6.2, to `<board-dir>/placement/<scope>.json`. The strategy is kept: a re-run gives the same placement |
| 4 | Claude | Runs the script with `--flow`. Nothing else touches the board |
| 5 | Claude | Replies with the script's table and its last line |
| 6 | User | Reviews, moves what is wrong by hand, saves |

**T6.2 — The strategy**

| # | Key | Holds | Default |
|---|---|---|---|
| 1 | `scope` | the room or ref whose parts are placed, everything under it by `parent` | required |
| 2 | `anchor` | where the flow starts: `{"ref": "J5"}` a footprint outside the scope, `{"at": [x, y]}` in mm, or `{"edge": "below"}` — `below`, `above`, `left`, `right` of Edge.Cuts | required |
| 3 | `direction` | the way the flow runs: `R`, `L`, `U`, `D`. `below` and `above` take `R` or `L`; `left` and `right` take `U` or `D` | required |
| 4 | `blocks` | the blocks in flow order: each `{"block": "<room or ref>", "parent": "<ref>"}`, or `{"part": "<ref>"}` for one part alone. `parent` may be left out when the block names a part, holds one part directly, or has `blocks` of its own | required |
| 4a | `blocks` in a block | the same, one level down: the block's sub-blocks in flow order. Nested to any depth. The block's parts no sub-block holds are placed around the sub-blocks | none |
| 4b | `direction` in a block | the flow inside that block and below | the outer one |
| 5 | `except` | refs of the scope left where they are; kept clear of as obstacles | `[]` |
| 6 | `gap` | clearance between parts, mm | 1.0 |
| 7 | `block_gap` | clearance between blocks, and from the anchor, mm | 3.0 |

- Every part of the scope on the board is in exactly one block, or in
  `except`. No orphan
- A part of the scope with no footprint on the board — a block's own
  symbol — is left out and named on the last line
- A block is resolved inside the scope only: a room name used elsewhere on
  the record does not reach in

**T6.3 — The geometry**

| # | Step | Rule |
|---|---|---|
| 1 | Read | one read of the footprints, their pads, nets and bounding boxes without text. Pads and boxes are taken back to each footprint's own frame |
| 2a | Sub-blocks | a block with `blocks` is the same operation one level down: its sub-blocks laid in order along its direction, `block_gap` apart, their parents on one line; then its other parts as row 3. Each sub-block's parent faces the nets of the sub-blocks before and after it, and of the blocks around its block |
| 2 | Parent | at the block's origin, turned so its pads on earlier blocks' nets, and the anchor's for the first block, face back along the flow, and its pads on later blocks' nets face forward |
| 3 | Children | one at a time, the one with most pads on nets already laid first. Each is tried at 0, 90, 180, 270, and set at the clear spot, `gap` from all laid, where its pads are nearest the laid pads of the same nets. A net on more than half the scope's parts — ground, a rail — pulls nothing. Search grid 0.25 mm, reach 40 mm |
| 4 | Blocks | each block's near edge `block_gap` past the last block's far edge along the flow. Across the flow, every block's origin on one line: the anchor's line for `ref` and `at`; for `edge`, the line that keeps every block `block_gap` off the board |
| 5 | Keep-outs | every footprint outside the scope, and the board outline for `edge`. A block that hits one goes to the nearest clear spot, forward along the flow first — a step across costs three along — away from the board for `edge`, either side for `ref` and `at` |
| 6 | Write | one `update_items` for every part of the scope. `--dry-run` writes nothing and prints the table |
| 7 | Check | one read back: every part outside the scope where it was, every pad of the scope where the plan put it. A failure is named on the last line |
| 8 | Finish | the scope's parts selected |

