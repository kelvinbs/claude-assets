# symbol-draw

Copy or draw a symbol into the project library, and tell `parts_table` where
it is. The second tool of process 2.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `ref_table`, `aml_table`, `mpn_table`<br>`copy-kicad-part`, `datasheet-read` — run as commands | `lib/<nickname>.kicad_sym`<br>`sym-lib-table`<br>`board.db` — `parts_table.symbol`, `parts_table.source` |

```
python3 tools/board-build/tools/symbol-draw.py <board-dir> <ipn> [--from LIB:NAME]
python3 tools/board-build/tools/symbol-draw.py <board-dir> --all
```

`--all` is every part whose `symbol` is null **and** whose instances put it
on a page. A part on no page is not on a sheet and has no symbol — a bare
board, an enclosure, a host the board plugs into. The run names them and
leaves them. `kicad-update` reads a blank page the same way.

## Gather before picking

The pinout is read first — `datasheet-read`, the recorded datasheet. The
pick then runs knowing the part's pin count, and `copy-kicad-part`
refuses a symbol with any other count. A part with no readable pinout is
picked on the guidelines alone. The pins in hand serve the draw when the
pick returns null — one read either way.

## The fields

A symbol this skill copies or draws carries the part's fields, written
from the record per T2.11 — `Value` is the IPN; `Footprint`,
`Description`, `note` from `parts_table`; `MPN`, `Manufacturer`,
`Datasheet` from the blank-rank approval. `kicad-update --push` rewrites
them later when the record changes.

## The order of the resorts

Drawing a symbol is the last thing tried, not the first.

| | |
|---|---|
| 1 | `copy-kicad-part` is run. It finds the symbol, copies it into `lib/` and renames its pins, and returns what it wrote |
| 2 | nothing holds it — `datasheet-read` is called and what it returns is drawn |

The library named by `--from` is a stock KiCad library if one of that
nickname is in the KiCad symbol directory, otherwise a path to a
`.kicad_sym` on disk. `KICAD_SYMBOL_DIR` overrides where the stock libraries
are looked for.

A KiCad library is an input, per T5.1. The symbol is copied into `lib/` and
owned from that point — section 2 requires every object to resolve inside the
repository, so nothing is left pointing at a library that only exists on this
machine. A derived symbol has its parent's graphics folded in on the way, for
the same reason: an `extends` in a copied symbol points outside the library
it landed in.

## The name and the fields

The symbol is named for the part's `name` when one is set, else the
IPN, and `parts_table.symbol` records whichever was used (n0.3). One
name, one row, one object.

| Property | Takes |
|---|---|
| `Reference` | the reference prefix of the part's class, T2.10 |
| `Value` | the IPN. `kicad-update` decides what an instance shows |
| `Description` | `parts_table.description` |
| `Datasheet` | `mpn_table.datasheet` of the blank-rank MPN |
| `Footprint` | left empty. `footprint-draw` owns it |

The class table is read from `table-write.py`, where it is already written
once, rather than repeated here.

## source

`source` is two letters, symbol then footprint, per T2.5. This tool writes
the first and leaves the second as it found it.

| Letter | When |
|---|---|
| `s` | copied from a KiCad stock library |
| `v` | copied from any other library on disk |
| `h` | drawn here, from the pinout |

## The drawing

The body is sized to its own pins. Left and right run down the sides in the
order the pinout gives, right mirrored so the symbol reads like the package
drawing; top and bottom run left to right at twice the pitch, clear of the
corners, so a name reading up the body does not land on its neighbour.


## The stage's primary pass

`--copy-only` is the Update-library stage's first pass, per T4.1: the
copy resort only. A part no library holds is reported and left — drawing
is secondary and waits for the User's word.

## Batch

`--all` runs the parts as a batch of worker subprocesses (n5.17 — the
parts share nothing), printing each part's output whole as it lands with
a progress line: timestamp, x of y, elapsed, remaining, ETA.

## Re-running

The library is merged, never rewritten. A symbol already in it is left alone
and a part that already carries a `symbol` is skipped, because either may
have been corrected by hand in the symbol editor.

`--redraw` replaces one. It is the only way this tool overwrites
anything. `--draw` skips the copy resort and draws from the pinout — the
User's word for secondary, given per part.

## What it refuses

- An IPN that does not read as one, or names no row
- No library holds the part and no pinout could be read — the part has no
  part number, or no datasheet was found for it
- A rename naming a pin the symbol does not have
- A `--from` that is not `<library>:<name>`
- A library nickname that resolves nowhere
- A symbol the named library does not hold
- A derived symbol whose parent is missing from its own library
- `--all` with `--from`, which names one symbol and so names one part
- A project folder with no `board.db`, or one missing a table

Each exits non-zero and names what it found. With `--all`, one part failing
does not stop the rest; the run lists them at the end and exits non-zero.

## After a run

```
kicad-cli sym export svg --output /tmp/sym <board-dir>/lib/<nickname>.kicad_sym
```

Every symbol plots, or the library does not parse. It does not check a pin
number against the datasheet — nothing downstream does. That check is reading
the datasheet, and it is `datasheet-read`'s prompt that carries it.
