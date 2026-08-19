# copy-kicad-part

Find a symbol for a part in the KiCad libraries. It answers one question —
does a symbol for this part already exist — and prints `<library>:<symbol>`
or `null`.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `aml_table`<br>KiCad symbol libraries | nothing |

```
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> <ipn> [--lib DIR ...]
```

It copies nothing. What it prints is what `symbol-draw --from` takes:

```
symbol-draw.py <board-dir> <ipn> --from $(copy-kicad-part.py <board-dir> <ipn>)
```

Drawing a symbol is the last resort. This tool is what is asked first, and
`null` is the answer that sends the part on to the next resort.

## Null is an answer

A part no library holds is a part that has to be drawn. That is not a
failure and the run exits zero saying `null`. The exit codes are for a
question that could not be asked at all — an IPN that names no row, a part
with no part number, no library directory to search.

## What counts as a match

The part number, and nothing else. Three ways, in order:

| | Example |
|---|---|
| the symbol's name is the part number | `ADA4807-4ARUZ` |
| the name is the part number without its ordering suffix | `USB3343` for `USB3343-CP-TR` |
| the name is a KiCad family the part number completes | `STM32H735VGTx` for `STM32H735VGT6` |

The trailing `x` is KiCad's own convention for the letters that choose a
package or a temperature grade. It stands for what the part number
completes, and up to three characters of it.

Nothing looser. A symbol that is the same kind of part is not this part, and
a wrong symbol on a sheet costs more than drawing the right one. A part that
is genuinely similar and needs its pins edited is a copy the User asks for by
name, through `--from`.

Every approved part number is tried, not only the one designed against. The
alternatives of T1.3 take the board as designed, so a symbol for one is a
symbol for all.

## Which libraries

The KiCad stock libraries, found the way `symbol-draw` finds them, and
`KICAD_SYMBOL_DIR` overrides. `--lib` adds a directory of `.kicad_sym`
files and is repeatable — a manufacturer's library on disk is searched the
same way.

Exact matches beat family matches. Two matches of the same rank are settled
by name, so the answer does not move between runs.

## What it refuses

- An IPN that does not read as one, or names no row
- A part with no row in `aml_table` — the part number is what is searched for
- A `--lib` that is not a directory
- No KiCad symbol directory found
- A board directory with no `board.db`, or one missing a table

Each exits non-zero and names what it found.
