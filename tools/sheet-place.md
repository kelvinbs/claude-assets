# sheet-place

Place symbols on their page. The tool of process 3.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`<br>`lib/<project>.kicad_sym` | `<project>.kicad_sch` — the root<br>`<project>-<page>.kicad_sch` — one per page<br>`<project>.kicad_pro`, written once |

```
python3 tools/board-build/tools/sheet-place.py <board-dir> [--project NAME]
```

The User wires the sheet afterwards. That is the point of the tool: it puts
the parts on the page so there is something to wire.

## What gets drawn

An instance of `ref_table` that carries a `page`, whose part carries a
`symbol`. Everything else is reported and left:

- a page and no symbol — `symbol-draw` has not reached it yet
- no page — it belongs to no sheet, and the run says which

Both are counted at the end of the run. Neither stops it.

## The UUID

`ref_table.uuid` is the symbol's UUID in the sheet, per T1.2. It is what
makes the symbol on the page and the row in the table the same thing on
every run, and what `board-place` follows from the footprint back to the
instance.

`table-write` mints it when the instance is created. This tool writes it
into the sheet and never invents one.

## Where a thing goes

T1.4, in its order.

| Rank | |
|---|---|
| `page` | selects the file |
| `room` | a block of the sheet. Rooms run in name order |
| family | what is left, grouped by the class letter of the IPN |

A new room or a new family starts a new row on the sheet, so the groups read
as groups. Within a group parts run by reference number, left to right,
wrapping at the page edge.

Order between rooms and families is arbitrary and subject to re-entry: once
a part is on the page it does not move, so the order is only ever the order
new parts arrive in.

## The fields

| Field | Takes |
|---|---|
| `Reference` | `ref_table.ref` |
| `Value` | the blank-rank MPN of `aml_table`, or the description when the part has no part number yet |
| `Footprint` | `parts_table.footprint`, hidden |
| `ipn` | `parts_table.ipn`, hidden. The key back to the record, T1.1 |

`Value` shows the part the board was designed against, because that is what
a person reads on a sheet. The IPN is the key and travels in its own field,
where nothing about how the part is bought can disturb it.

## Re-entry

This is the tool where the re-entry rule of T3.1 earns its keep. The User's
wires are on these pages.

- A page that exists is **edited, not rewritten**. New symbols are appended
  below what is already drawn; every wire, label, junction and graphic stays
  exactly where it is.
- A symbol whose UUID is already on the page is left alone — position,
  rotation, field placement and all.
- The root sheet gains a sheet symbol for a new page and keeps the ones it
  has.
- `<project>.kicad_pro` is written once and never touched again.

Close the editor before running. KiCad holds the file in memory and will
write it back over anything added underneath it.

## Paper

A new page takes the smallest ANSI size, `A` to `E`, that its parts fit on.
A page that already exists keeps the size it has.

## What it refuses

- A board directory with no `board.db`, or one missing a table
- No `.kicad_pro` and no `--project`
- A `symbol` naming a library that is not this project's — section 2 does
  not allow a sheet to point outside the repository, and `symbol-draw`
  copies the object in before it is placed
- A `symbol` the project library does not hold
- Nothing to place at all

Each exits non-zero and names what it found.

## What it is not

It does not run ERC and it does not run DRC. Electrical checking is not this
tool's business — the tool puts parts where the record says they go, and the
verdict on a design belongs to the User and to KiCad.
