# table-write

Create or modify a part. The skill of Update parts.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table` | `board.db` — `parts_table`, `ref_table` |

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
table-write.py <board-dir> show   [<ipn>]
```

Anywhere a verb takes an IPN, the part's `name` or an approved MPN
serves instead — resolved name first, then MPN, then IPN (n0.3).
`--name` on `add` and `set` writes the name: yours, unique.

## add

Creates one `parts_table` row and the `ref_table` rows that go with it.

`--class` is the IPN letter of T2.10. The tool takes the next free number
in that class, so the IPN is given by the tool.

`--count` is how many instances to create, default 1. Each takes the lowest
free number for its class's reference prefix and a fresh UUID.

`--description` is required. A part with no description is a row nobody can
read six months later.

`--note` is the other `parts_table` field it writes.

`--parent` names the instance this one serves — the op-amp instance a
feedback resistor closes the loop around. Parenthood is a property of use,
so it is a reference, resolves to that instance's `uuid`, and goes on the
`ref_table` rows with `--page` and `--room`. `show <ipn>` lists an
instance's children.

There is no status to set. The record says what the design is, not how far
along it is — see T2.3.

## set

Changes fields on a part that exists. Prints what each field was and what it
became, so a change is legible in the terminal as well as in the database.

## place

Raises the instance count to `--count`, adding the difference. `--page` and
`--room` are applied to every instance of the part.

It will not lower a count. An instance is a thing on a sheet with a UUID that
a footprint may already point at, and losing one silently is how a board
loses a part. Removing one is `drop`, and it names the reference.

## parent

Sets one instance's parent: `parent R3 --under U1`. `--none` clears it. It
refuses a reference that names no instance, and a parent chain that closes
a loop.

## mpn — retired into set

The part number is a column on the part (n0.4): `set <part> --mpn ...`,
with `--manufacturer` and `--datasheet` beside it. A prototype buys one
part one way.

## drop

Removes one instance by its reference. One at a time, and it says which part
it came out of.

## show

Every part, its class, its status and its instances. With an IPN, that part
alone, and its source, symbol and footprint as well.

## The library fields

`symbol`, `footprint` and `source` are written by `symbol-draw` and
`footprint-draw` in the Update library stages, after the object is copied
into `lib/` and given the project nickname. This tool leaves them alone.

## What it refuses

- A class letter that is not in T2.10
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
