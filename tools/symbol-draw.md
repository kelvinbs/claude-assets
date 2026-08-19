# symbol-draw

Copy or draw a symbol into the project library, and tell `parts_table` where
it is. The second tool of process 2.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `aml_table`, `mpn_table`<br>`<board-dir>/parts/<IPN>.json`<br>KiCad symbol libraries | `lib/<nickname>.kicad_sym`<br>`sym-lib-table`<br>`board.db` — `parts_table.symbol`, `parts_table.source` |

```
python3 tools/board-build/tools/symbol-draw.py <board-dir> <ipn> [--from LIB:NAME]
python3 tools/board-build/tools/symbol-draw.py <board-dir> --all
```

`--all` is every part whose `symbol` is null.

It calls `lib-init` first, every run, so the library and its `sym-lib-table`
entry exist before anything is written to them.

## Two ways in

| | |
|---|---|
| `--from <library>:<name>` | the symbol exists. It is copied in |
| absent | it does not. It is drawn from `parts/<IPN>.json` |

The library named by `--from` is a stock KiCad library if one of that
nickname is in the KiCad symbol directory, otherwise a path to a
`.kicad_sym` on disk. `KICAD_SYMBOL_DIR` overrides where the stock libraries
are looked for.

A KiCad library is an input, per T1.2. The symbol is copied into `lib/` and
owned from that point — section 2 requires every object to resolve inside the
repository, so nothing is left pointing at a library that only exists on this
machine. A derived symbol has its parent's graphics folded in on the way, for
the same reason: an `extends` in a copied symbol points outside the library
it landed in.

## The name and the fields

The symbol is named for the IPN, and `parts_table.symbol` becomes
`<nickname>:<IPN>`. One name, one row, one object.

| Property | Takes |
|---|---|
| `Reference` | the reference prefix of the part's class, T1.2 |
| `Value` | the IPN. `sheet-place` decides what an instance shows |
| `Description` | `parts_table.description` |
| `Datasheet` | `mpn_table.datasheet` of the blank-rank MPN |
| `Footprint` | left empty. `footprint-draw` owns it |

The class table is read from `table-write.py`, where it is already written
once, rather than repeated here.

## source

`source` is two letters, symbol then footprint, per T1.2. This tool writes
the first and leaves the second as it found it.

| Letter | When |
|---|---|
| `s` | copied from a KiCad stock library |
| `v` | copied from any other library on disk |
| `h` | drawn here, from the pinout |

`--source s|v|h` overrides it — a vendor library sitting in the stock
directory, or a stock symbol fetched from elsewhere.

## The drawing

The body is sized to its own pins. Left and right run down the sides in the
order the pinout gives, right mirrored so the symbol reads like the package
drawing; top and bottom run left to right at twice the pitch, clear of the
corners, so a name reading up the body does not land on its neighbour.

This is the code that drew the archived proto1 library. It is unchanged.

## Re-running

The library is merged, never rewritten. A symbol already in it is left alone
and a part that already carries a `symbol` is skipped, because either may
have been corrected by hand in the symbol editor.

`--redraw` replaces one. It is the only way this tool overwrites anything.

## What it refuses

- An IPN that does not read as one, or names no row
- No `--from` and no pinout on disk — run `datasheet-read` first
- A `--from` that is not `<library>:<name>`
- A library nickname that resolves nowhere
- A symbol the named library does not hold
- A derived symbol whose parent is missing from its own library
- `--all` with `--from`, which names one symbol and so names one part
- A board directory with no `board.db`, or one missing a table

Each exits non-zero and names what it found. With `--all`, one part failing
does not stop the rest; the run lists them at the end and exits non-zero.

## After a run

```
kicad-cli sym export svg --output /tmp/sym <board-dir>/lib/<nickname>.kicad_sym
```

Every symbol plots, or the library does not parse. It does not check a pin
number against the datasheet — nothing downstream does. That check is reading
the datasheet, and it is `datasheet-read`'s prompt that carries it.
