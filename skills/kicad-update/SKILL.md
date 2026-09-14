---
name: kicad-update
description: Place instances on the sheets, push record fields to library and instances, pull library fields back. Stages 4 and 6.
---

# kicad-update

Place instances, push record to library fields, pull library fields to
record. The skill of stages 4 and 6.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `net_table`, `bus_table`<br>`lib/<project>.kicad_sym`<br>`<project>-<page>.kicad_sch` — what the User placed | `<project>.kicad_sch` — the root, rewritten every run<br>`<project>-<page>.kicad_sch` — one per page, root page or sub-sheet<br>`<project>.kicad_pro`, written once; `schematic.bus_aliases` kept current<br>`lib/<project>.kicad_sym` — the fields, on push<br>`board.db` — `ref_table`; `parts_table` on pull |

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-update/kicad-update.py <board-dir> [--assign <uuid>=<ipn> ...]
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-update/kicad-update.py <board-dir> --push
python3 ${CLAUDE_PLUGIN_ROOT}/skills/kicad-update/kicad-update.py <board-dir> --pull
```

## The three verbs

| Verb | Direction | Does |
|---|---|---|
| place, the default | record to sheets | rewrites the root; draws missing drawings with a label on every pin `net_table` names, sheet symbols for sub-sheet instances, a breakout for every bus that leaves a page; enters User-placed symbols. Instance fields written once at placement: `Reference`, `ipn`, and the library fields copied |
| `--push` | record to library, sheets and board | rewrites the root; rewrites every library symbol's fields, and every placed drawing's, from the record — T2.11, with the per-instance block, one path and reference per instance. Then the labels: every label on the page is deleted, wherever it sits, and every `net_table` row written back as one at the pin's current place; sheet symbols get their pins, stubs and labels afresh; the port area is redrawn. Then the board: every footprint whose Reference the record holds takes its symbol's sheet path; one the record does not hold is reported. Graphics, positions, wires and routing untouched |
| `--pull` | library to record | reads library fields back onto the part: `Description`, `Value`, `Footprint`, `note`, `Manufacturer`, `Datasheet`. `MPN` is reported on mismatch, never written — it is the record's |

The User's UI for part data is the Symbol Editor: edit the field there,
then `--pull`. Claude's is `table-write`, then `--push`. Instances take
changed fields on the next `--push`.

The User wires the sheet afterwards. That is the point of the tool: it puts
the parts on the page so there is something to wire.

## What gets drawn

A drawing of `ref_table` that carries a `page`, whose part carries a
`symbol`. A drawing is one uuid; in a sub-sheet it stands for one row per
instance of the sheet, and its per-instance block lists every path with
that row's reference. Everything else is reported and left:

- a page and no symbol — `copy-kicad-part` has not reached it yet
- no page — it belongs to no sheet, and the run says which
- a drawing in a sub-sheet whose part has no instance yet — no sheet to
  be in

All are counted at the end of the run. None stops it.

## The UUID

`ref_table.uuid` is the symbol's UUID in the sheet, per T2.4. With `path`
it is what makes the symbol on the page and the row in the table the same
thing on every run, and what this tool follows from the footprint back to
the instance.

`table-write` mints it when the drawing is created. This tool writes it
into the sheet and never invents one.

## Labels — never global

Every pin the record names carries a local `label`, nothing else. A net
that leaves the file, by T2.6c of `board-build-tool.md`, has its
`hierarchical_label` in the file's port area, joined to a local label of
the same name. A net in a bus leaves as the bus.

| Drawn | Where | Kept where the User left it |
|---|---|---|
| local label at a pin end | every drawing's named pin, where the pin is now | no — every label on the page is deleted and written afresh on push |
| sheet symbol, pins down the right edge, a stub and a local label at each pin | a sub-sheet instance, on the page that placed it | position yes; pins and fittings rewritten on push |
| port — hierarchical label, stub, local label | the port area, one per leaving net | no — the port area is the tool's, top right, redrawn every run |
| bus breakout — hierarchical bus label, bus, an entry per member the file uses, a local label at each | the port area, one per bus that leaves | no — as above |
| the root — a sheet symbol per root page, a stub and a root label at every pin | `<project>.kicad_sch` | no — the root is the tool's |

The User wires nothing to make the pages connect: the label at the pin
joins the port or the breakout by name, those leave by the sheet pin, the
root joins the pins. Wires are drawn for what is not a named net.

Bus aliases go to `schematic.bus_aliases` in `<project>.kicad_pro`, the one
key the tool touches there after init.

## Sub-sheets

A part of class `B` is a sub-sheet. Its `name` is the page it is drawn on,
`<project>-<slug>.kicad_sch`; its instances are sheet symbols on the page
that placed them, named by their reference, `SH1`, `SH2`. What is drawn on
the sub-sheet page is drawn once; every instance path is written into each
symbol's per-instance block with that instance's reference, so KiCad
annotates the four copies as the record does.

A sheet symbol's pins are the nets its sub-sheet exports (T2.6c) — what
leaves the file, and what any instance names on a pin. The label at a pin
on the page above comes from `net_table` on the instance row: `VOUT` on
`SH1` joins `6V0`. A bus pin is named `{BUS}` and takes a bus stub.

## Where a thing goes

T2.12, in its order.

| Rank | |
|---|---|
| `page` | selects the file |
| `room` | a block of the sheet. Rooms run in name order |
| `parent` | a parent and its children, together |

Parts run left to right and wrap at the page edge. The sort puts a group's
members next to each other, so they read in order.

Order between rooms is arbitrary and subject to re-entry: once
a part is on the page it does not move, so the order is only ever the order
new parts arrive in.

## The fields

| Field | Takes |
|---|---|
| `Reference` | `ref_table.ref` |
| `Value` | `parts_table.value` |
| `Footprint` | `parts_table.footprint`, hidden |
| `ipn` | `parts_table.ipn`, hidden. The key back to the record, T2.11 |

## Re-entry

This is the tool where the re-entry rule of section 4.3 earns its keep. The User's
wires are on these pages.

- A page that exists is **edited, not rewritten**. New symbols are appended
  below what is already drawn; every wire, label, junction and graphic stays
  exactly where it is, but for the tool's own fittings — pin labels, sheet
  pins, the port area — which it redraws.
- A symbol whose UUID is already on the page is left alone — position,
  rotation, field placement and all. A sheet symbol keeps its place; its
  pins follow the record.
- The root is rewritten every run. A page keeps its sheet uuid and page
  number.
- `<project>.kicad_pro` is written once; only `schematic.bus_aliases` is
  kept current after that.

Close the editor before running. KiCad holds the file in memory and will
write it back over anything added underneath it.

## The return direction

The record is master for the part — section 2.2. The sheet is where the
User works, and a part placed there by hand is a real use of a real part.
Each run reads every page back and settles the difference both ways.

| Found | Done |
|---|---|
| Instance in the record, not on its page | Placed — the forward direction above |
| Symbol on a page, uuid not in the record, `ipn` field names a part | Entered in `ref_table` under the symbol's own uuid, with its `Reference` and page — one row per instance of the sheet when the page is a sub-sheet. The field values are then rewritten from the record |
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

## rename

`--rename OLD NEW` renames a part in one pass: `parts_table.name`, the
library symbol when it carries the old name, every sheet `lib_id`, and
the part file's name tail. A deliberate act with mechanical
propagation — nothing rots (n0.3).

## The report

The root, then one line per page, sub-sheets last: placed, left, entered. Then the
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

A new page takes the smallest ANSI size, `A` to `E`, that its parts fit on
with the port area's width kept clear at the right edge. Drawings and the
port area start 12 grid from the top, so a label on a top pin clears the
frame. A page that already
exists keeps the size it has; a run says when its drawing reaches into the
port area, and placing that page afresh is the fix.

## What it refuses

- A project folder with no `board.db`, or one missing a table
- An empty `project_table` — the record names the project (T2.13)
- A `symbol` naming a library that is not this project's — section 2 does
  not allow a sheet to point outside the repository, and `copy-kicad-part`
  copies the object in before it is placed
- A `symbol` the project library does not hold
- Nothing to place at all

Each exits non-zero and names what it found.

## What it is not

It does not run ERC and it does not run DRC. Electrical checking is not this
tool's business — the tool puts parts where the record says they go, and the
verdict on a design belongs to the User and to KiCad. `kicad-cli sch erc`
on the root is the check; a net the record names on one pin only reads as
an isolated label there, and that is the record's to answer.
