---
name: table-write
description: Create or modify a part in board.db: add, set, place, parent, drop, show; tag a simulation: sim. Stage 2.
---

# table-write

Create or modify a part. The skill of Update parts.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `net_table`; the part files' pins | `board.db` — `parts_table`, `ref_table`, `price_table`, `net_table`, `bus_table`, `sim_table`, `sim_net_table` |

Part fields reach KiCad by `kicad-update --push`. It never opens a KiCad file, and of the sourcing tables it touches only
`init-pipeline` must have run first.

It sets `PRAGMA foreign_keys = ON` on every connection, because SQLite leaves
them off otherwise and the keys of T2.9 would not be checked.

```
table-write.py <board-dir> add    --class A --description "..." [options]
table-write.py <board-dir> set    <ipn> [--field value ...]
table-write.py <board-dir> place  <ipn> --count N [--page P] [--room R]
table-write.py <board-dir> parent <ref> --under <ref> | --none
table-write.py <board-dir> mpn    <ipn> <mpn> [--rank N] [--note ...]
table-write.py <board-dir> drop   <ref>
table-write.py <board-dir> price  <ipn> --vendor V --vendor-pn PN [--stock N] [--checked DATE] [--library L] --break QTY:PRICE ...
table-write.py <board-dir> net    <ref> <pin> <name> | --none
table-write.py <board-dir> bus    [<name> <net>... | --drop <net>...]
table-write.py <board-dir> unplace <ref>
table-write.py <board-dir> room   <ref> <name> [--under <room|ref|ref.unit>] | --none
table-write.py <board-dir> board  <name> --page P | --ref R | --none | --show
table-write.py <board-dir> sim    add <name> <kind> --block <ref> [--block <ref> ...]
table-write.py <board-dir> sim    set <name> <net> <source> | <net> --none | --directive <line>
table-write.py <board-dir> sim    drop <name>
table-write.py <board-dir> sim    show
table-write.py <board-dir> show   [<ipn>]
```

Anywhere a verb takes an IPN, the part's `name` or an approved MPN
serves instead — resolved name first, then MPN, then IPN (n0.3).
`--name` on `add` and `set` writes the name: yours, unique.

## add

Creates one `parts_table` row and the `ref_table` rows that go with it.

`--class` is the IPN letter of T2.10. The tool takes the next free number
in that class, so the IPN is given by the tool.

`--count` is how many drawings to create, default 1. Each takes a fresh
UUID and, on a root page, one row with the lowest free number for its
class's reference prefix. On a sub-sheet page it takes one row per instance
of the sheet, each with its own reference (T2.4).

`--class B` is a sub-sheet. It needs `--name`: the page it is drawn on.
Its `symbol` is set to `sheet:<name>`; its instances annotate as `SH`. Place
the sheet on a page first, then place parts on the sheet's page.

`--description` is required. A part with no description is a row nobody can
read six months later.

`--value` and `--note` are the other `parts_table` fields it writes.

`--parent` names the instance this one serves — the op-amp instance a
feedback resistor closes the loop around. Parenthood is a property of use,
so it is a reference, resolves to that instance's `id`, and goes on the
`ref_table` rows with `--page` and `--room`. `show <ipn>` lists an
instance's children.

There is no status to set. The record says what the design is, not how far
along it is — see T2.3.

## set

Changes fields on a part that exists. Prints what each field was and what it
became, so a change is legible in the terminal as well as in the database.
`--value` is what the sheet shows as Value. `--sim-model` is `R`, `C`, `L`,
`opamp` or `rnet`, what the part is to the simulator; `--sim-params` its
parameters, T2.3. `--footprint` takes the project footprint, `<nickname>:<name>`, written by
the session at Update library — footprints, 3D (`copy-kicad-part.md`,
Footprints).

## place

Raises the drawing count to `--count`, adding the difference. `--page` and
`--room` go on the rows added, and on any row of the part still without a
page. A part placed on a sub-sheet page gets one row per instance of the
sheet. Placing another instance of a class-`B` part adds a row for every
drawing already on its page, with fresh references.

It will not lower a count. An instance is a thing on a sheet with a UUID that
a footprint may already point at, and losing one silently is how a board
loses a part. Removing one is `drop`, and it names the reference.

## parent

Sets one instance's parent: `parent R3 --under U1`. `--none` clears it. It
refuses a reference that names no instance, and a parent chain that closes
a loop. Every row under the reference takes the parent: every unit of a
package, and in a sub-sheet the drawing's every row, matched instance for
instance when the parent is drawn in the same sheet.

## mpn — retired into set

The part number is a column on the part (n0.4): `set <part> --mpn ...`,
with `--manufacturer` and `--datasheet` beside it. A prototype buys one
part one way.

## price

Records one vendor's survey of a part: one `price_table` row per break,
upsert on the vendor part number and break. `--stock` and `--checked`
date the survey. `--library` is the vendor's tier for that part number,
at JLCPCB `basic` or `extended`; an extended part carries a loading fee
per part number per order. A survey given without it keeps the tier
already recorded.

## drop

Removes one instance by its reference. One at a time, and it says which part
it came out of. A sub-sheet instance takes the rows drawn under it. A
drawing in a sub-sheet is one drawing, so its rows go together. A drawing's
nets go with its last row.

## net

Names the net on one pin of one drawing: one `net_table` row, upsert on
the drawing and pin. The drawing is named by any of its references. On a
multi-unit package the pin picks the unit: the part file's `units` says
which unit draws it, and the row goes on that unit's drawing. A reference
names the package, so `net U14 5` reaches U14B without naming it.
`--none` clears it. The sheet takes a label at that pin on the next
`kicad-update` place or push — local or hierarchical per T2.6c, never
global — and loses it on the push after the row goes. On a sub-sheet
instance the pin is a name the sub-sheet exports, `VOUT`, or a bus,
`{RAILS}`; the net is what it joins on the page above.

## room

Puts an instance in a room: `room R3 minus --under U15.2`. A room is a
row of `ref_table` with `kind = 'room'`, T2.4, with its own `id` and a
parent, so rooms nest. `--under`
names what the room sits in — a room, an instance, or one unit of a
package, written `U15.2`. Without it the room takes the parent the
instance has today, so nothing moves but the level. `--none` puts the
instance back under its room's own parent.

The room is found or created on the instance's page under that parent,
so naming the same room twice puts both instances in it.

`--corner nw|ne|sw|se` says which corner of the box the room's name is
placed at. Null is `nw`. It is a property of the room, so two rooms on
one package may label away from each other.

## sim

A simulation instance, T2.6d and T2.6e: what KiCad runs when the User
presses Run. `sim add bbfilt ac --block BB_Block` tags the blocks; the
kind is `ac`, `tran`, `dc` or `op` and sets a default directive. The
blocks' descendants by the parent chain are the simulated parts. Their
boundary nets, those shared with a part outside the blocks, get a source
row each: a rail named as a voltage, `3V3`, `-5V0`, gets `dc <V>`; a net
an outside `output` pin drives, per the part file, gets `ac 1`; the rest
are printed with no source. `GND` is ground and gets nothing. `sim set
<name> <net> <source>` sets or adds a source, `--none` removes it,
`--directive` replaces the spice line. `sim drop` removes the instance
and its rows. `sim show` lists every instance. `kicad-update --push`
draws the sources and the directive, and marks everything outside the
blocks excluded from simulation.

## board

Which board a node is on: `board rf --page RF`. `board` is a column of
`ref_table` beside the room — the page selects the sheet file, the board
selects the physical board. A page is on one board, so the column is set
by page; `--ref` sets one instance, `--none` clears it, `--show` prints
board by page. A row with a page and no board takes its page's board on
the next run of any verb, so a part placed later does not rot.

## unplace

Clears an instance's place and mark, every row of the drawing. The symbol
stays on the sheet where it is; the packer lays it the next time the page
is placed afresh.

## bus

Groups nets into a bus: `bus RAILS 3V3 5V0 GND`. A net is in one bus; naming
it again moves it. `bus --drop GND` takes nets out. `bus` alone lists.
Members keep their names; `kicad-update` writes the alias to the project
and a breakout on every page the bus leaves.

## show

Every part, its class, its status and its instances. With an IPN, that part
alone, and its source, symbol and footprint as well.

## The library fields

`symbol`, `footprint` and `source` are written in the Update library
stages, after `copy-kicad-part` has copied the object into `lib/` under the
project nickname. `set --footprint` writes the footprint. `symbol` and
`source` have no `set` option; they are written to `board.db` directly.

## What it refuses

- A class letter that is not in T2.10
- A pin no unit of the part draws, and a multi-unit part whose part file
  has no `units` — the unit cannot be known and a guess writes a row on a
  drawing that has no such pin
- A `sim_model` not `R`, `C`, `L`, `opamp` or `rnet`; a `sim add` kind not `ac`,
  `tran`, `dc` or `op`; a `--block` that names no instance; a `sim` name
  already taken, or one that names no instance on `set` and `drop`
- A `parent` or `--under` that names no instance, or closes a loop
- An IPN that does not read as one, or names no row
- A reference that names no instance
- Lowering an instance count
- `add` with no description
- A project folder with no `board.db`, or one missing a table

Each exits non-zero and names what it found.

## Numbering

A reference number is free when no row holds it. `drop U4` then `place` puts
`U4` back. The IPN number is not reused — the highest in the class is the
mark, so a deleted part does not hand its number to the next one.

Nothing here annotates a sheet. These references are the tool's;
`kicad-update` writes them out.
