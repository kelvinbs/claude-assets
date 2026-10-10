---
name: block-outline
description: Box each named group of parts — a room or a part and everything under it — on the open board, on User.Comments, labelled with its name, and generate the table of contents of every box. Stage 6.
---

# block-outline

Draws a labelled box around each named group of parts on the open board,
and generates the table of contents of every box on the layer. No part
moves.

| Reads | Writes |
|---|---|
| `board.db` — `ref_table`<br>the board open in KiCad, live | the board open in KiCad — rectangles, labels and the table of contents on User.Comments. Nothing saved |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/block-outline/block-outline.py <board-dir> [<room|ref> ...]
```

## 1 — The group

**T1 — What a name selects**

| # | Name | Group |
|---|---|---|
| 1 | a room, `ref_table.name` | the room and everything under it by `parent` |
| 2 | a part, `ref_table.ref` | the part and everything under it by `parent` |
| 3 | a name held by nested rooms | the outermost; the inner ones are inside it |
| 4 | a name held by unrelated nodes | refused; name a part instead |

- Only nodes and parts of the open board: `ref_table.board` against the
  `-board-<board>` of the file's name.

## 2 — The box

**T2 — What is drawn**

| # | Item | Value |
|---|---|---|
| 1 | Layer | User.Comments |
| 2 | Rectangle | the group's footprints, bounding boxes without text, 0.5 mm outside; line 0.1 mm |
| 3 | Label | the name, 1 mm text, left-aligned, its baseline 0.3 mm above the box's top left corner |
| 4 | Re-run | a name's label on User.Comments and the rectangle whose corner sits under it are removed before the new box is drawn |
| 5 | Table of contents | one text, `Contents` and under it every box label on User.Comments, sorted; 1 mm, left-aligned, top 40 mm left of the board outline's top left corner. Generated on every run; with no names, only it is generated |
| 6 | Calls | one read of the board, one remove, one create |

- A group with no footprint on the board is named on the line, and no box
  is drawn for it.

## 3 — What it refuses

- No `board.db` in the board folder
- A name that is neither a room nor a ref on the open board
- A name held by unrelated nodes, T1 row 4
- No board open in KiCad
- An open board with no outline on Edge.Cuts
