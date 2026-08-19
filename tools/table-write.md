# table-write

Create or modify a part. The tool of process 1.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table` | `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table` |

It never opens a KiCad file, and of the sourcing tables it touches only
`aml_table`. `db-init` must have run first.

It sets `PRAGMA foreign_keys = ON` on every connection, because SQLite leaves
them off otherwise and the keys of T1.5 would not be checked.

```
table-write.py <board-dir> add   --class A --description "..." [options]
table-write.py <board-dir> set   <ipn> [--field value ...]
table-write.py <board-dir> place <ipn> --count N [--page P] [--room R]
table-write.py <board-dir> mpn   <ipn> <mpn> [--rank N] [--note ...]
table-write.py <board-dir> drop  <ref>
table-write.py <board-dir> show  [<ipn>]
```

## add

Creates one `parts_table` row and the `ref_table` rows that go with it.

`--class` is the IPN letter of T1.2. The tool takes the next free number in
that class, so the IPN is given by the tool.

`--count` is how many instances to create, default 1. Each takes the lowest
free number for its class's reference prefix and a fresh UUID.

`--description` is required. A part with no description is a row nobody can
read six months later.

`--parent` and `--note` are the other `parts_table` fields it writes.

`--parent` names the part this one serves — the op-amp a feedback resistor
closes the loop around. It must name a part that exists and cannot be the
part itself. `show <ipn>` lists a part's children, which is its assembly. `--page` and `--room` are properties of the instance
and go on the `ref_table` rows.

There is no status to set. The record says what the design is, not how far
along it is — see T1.2.

## set

Changes fields on a part that exists. Prints what each field was and what it
became, so a change is legible in the terminal as well as in the database.

## place

Raises the instance count to `--count`, adding the difference. `--page` and
`--room` are applied to every instance of the part.

It will not lower a count. An instance is a thing on a sheet with a UUID that
a footprint may already point at, and losing one silently is how a board
loses a part. Removing one is `drop`, and it names the reference.

## mpn

Records an approval in `aml_table` — this manufacturer part may be built
against this IPN. Choosing a part and choosing the part number it is bought
as are the same act, which is why it is here and not in a sourcing tool.

Naming a part number creates its `mpn_table` row too, with every field but
the key empty. The part number's identity exists from the moment you name it;
what is in the package and who makes it gets filled in later, and nothing
that is fetched belongs there at all — see T1.3.

The row is the approval. A part number that may not be built does not get a
row, and neither does one that would need the board changed to take — every
MPN in the table drops in. There is no flag to set and none to forget.

`--rank` orders the alternatives and is left blank on the one you designed
against — see T1.3. Give a number only when there is something to order. The
database refuses a second blank rank for one IPN.

`--note` is a sentence a person writes, and is normally left alone — see
T1.2. The tool never fills it in.

Naming the same IPN and MPN again updates the rank and the note rather than
adding a second row. `aml_table` is kept, unlike the fetched
tables beside it, so it is never discarded and refetched.

## drop

Removes one instance by its reference. One at a time, and it says which part
it came out of.

## show

Every part, its class, its status and its instances. With an IPN, that part
alone, and its source, symbol and footprint as well.

## The library fields

`symbol`, `footprint` and `source` are written by `symbol-draw` and
`footprint-draw` in process 2, after the object is copied into `lib/` and
given the project nickname. This tool leaves them alone.

## What it refuses

- A class letter that is not in T1.2
- A `parent` that is not an IPN, names no row, or is the part itself
- An IPN that does not read as one, or names no row
- A reference that names no instance
- Lowering an instance count
- `add` with no description
- A board directory with no `board.db`, or one missing a table

Each exits non-zero and names what it found.

## Numbering

A reference number is free when no row holds it. `drop U4` then `place` puts
`U4` back. The IPN number is not reused — the highest in the class is the
mark, so a deleted part does not hand its number to the next one.

Nothing here annotates a sheet. These references are the tool's, and
`sheet-place` writes them out; T1.2 says what happens when KiCad renumbers.
