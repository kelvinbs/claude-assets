# kicad-update

Place instances, push record to library fields, pull library fields to
record. The skill of stages 4 and 6.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table`<br>`lib/<project>.kicad_sym`<br>`<project>-<page>.kicad_sch` — what the User placed | `<project>.kicad_sch` — the root<br>`<project>-<page>.kicad_sch` — one per page<br>`<project>.kicad_pro`, written once<br>`lib/<project>.kicad_sym` — the fields, on push<br>`board.db` — `ref_table`; `parts_table`, `mpn_table` on pull |

```
python3 tools/board-build/tools/kicad-update.py <board-dir> [--assign <uuid>=<ipn> ...]
python3 tools/board-build/tools/kicad-update.py <board-dir> --push
python3 tools/board-build/tools/kicad-update.py <board-dir> --pull
```

## The three verbs

| Verb | Direction | Does |
|---|---|---|
| place, the default | record to sheets | draws missing instances, enters User-placed symbols. Instance fields written once at placement: `Reference`, `ipn`, and the library fields copied |
| `--push` | record to library | rewrites every library symbol's fields from the record — T2.11. Graphics untouched |
| `--pull` | library to record | reads library fields back: `Description`, `Footprint`, `note` to `parts_table`; `Manufacturer`, `Datasheet` to the blank-rank MPN's `mpn_table` row. `MPN` and `Value` are reported on mismatch, never written — an approval is `table-write`'s act, and `Value` is the blank-rank MPN, else the description |

The User's UI for part data is the Symbol Editor: edit the field there,
then `--pull`. Claude's is `table-write`, then `--push`. Instances take
changed fields in KiCad — Update Symbols from Library.

The User wires the sheet afterwards. That is the point of the tool: it puts
the parts on the page so there is something to wire.

## What gets drawn

An instance of `ref_table` that carries a `page`, whose part carries a
`symbol`. Everything else is reported and left:

- a page and no symbol — `symbol-draw` has not reached it yet
- no page — it belongs to no sheet, and the run says which

Both are counted at the end of the run. Neither stops it.

## The UUID

`ref_table.uuid` is the symbol's UUID in the sheet, per T2.4. It is what
makes the symbol on the page and the row in the table the same thing on
every run, and what this tool follows from the footprint back to the
instance.

`table-write` mints it when the instance is created. This tool writes it
into the sheet and never invents one.

## Where a thing goes

T2.12, in its order.

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
| `ipn` | `parts_table.ipn`, hidden. The key back to the record, T2.11 |

`Value` shows the part the board was designed against, because that is what
a person reads on a sheet. The IPN is the key and travels in its own field,
where nothing about how the part is bought can disturb it.

## Re-entry

This is the tool where the re-entry rule of section 4.3 earns its keep. The User's
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

## The return direction

The record is master for the part — section 2.2. The sheet is where the
User works, and a part placed there by hand is a real use of a real part.
Each run reads every page back and settles the difference both ways.

| Found | Done |
|---|---|
| Instance in the record, not on its page | Placed — the forward direction above |
| Symbol on a page, uuid not in the record, `ipn` field names a part | Entered in `ref_table` under the symbol's own uuid, with its `Reference` and page. The field values are then rewritten from the record |
| Symbol on a page, no `ipn` field, or one naming no part | Reported, left as is. The LLM names the part from the symbol and its `Value`, creates it with `table-write` if it is new, and reruns with `--assign <uuid>=<ipn>`; the script writes the `ipn` field and enters the row |
| Symbol on a page whose `Reference` is held by another instance | Reported as a conflict, left as is. The User renames one |
| Symbol on both sides, a field differs | left alone. Fields flow to instances by Update Symbols from Library, not by this tool |
| Symbol on both sides, on a page other than `ref_table.page` | Reported. Moving a symbol between sheets would cut its wires |

The tool deletes on neither side. It cannot tell an instance the User
removed from a sheet from one it has not placed yet, and it keeps no state
to learn the difference — section 1.4. Removal is the User's: delete the
symbol on the sheet first, then `table-write drop` in the record. The other
order re-enters the symbol on the next run.

`--assign` is the one judgment the skill has. The script decides nothing
about which part a symbol is; it reports the symbol and applies the answer.

## The report

One line per page: placed, left, fields refreshed, entered. Then the
lists — unresolved symbols with their `Value` and `lib_id`, conflicts,
page mismatches, instances with no page, instances with no symbol. A
second run straight after the first reports zeros and empty lists; that is
the proof the two sides agree.

## Units

A multi-unit package is one instance. The record holds one row per unit —
same reference, own uuid, `unit` from 1 (T2.4). Where the symbol has more
units than the record has rows, place mints the missing rows in the
record, then draws every unit — U15A, U15B, and the power unit — side by
side. Fields ride on unit 1; the other units show the reference alone. No
pin is hidden: `copy-kicad-part` strips pin hiding as a symbol comes in.

## Paper

A new page takes the smallest ANSI size, `A` to `E`, that its parts fit on.
A page that already exists keeps the size it has.

## What it refuses

- A project folder with no `board.db`, or one missing a table
- An empty `project_table` — the record names the project (T2.13)
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
