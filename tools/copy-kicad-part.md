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

## The index, then one question

`lib-index` parses every library on the machine into one file — 22k symbols,
two seconds, no model. This tool scores that index against the part's number
and the words of its description, takes the candidates, and puts them and
their pins in the prompt.

The model reads nothing. It is given the candidates and asked which of them
is the part, so a part costs one short run rather than a search across
hundreds of files.

What comes back is checked against the candidate list: the symbol was one of
them, and every renamed pin is a pin it has.

## What counts as the part

A similar part's symbol is this part's symbol when its pins do the same job.
The package and the maker belong to the footprint, not the symbol, so a
donor in another package is not a reason to refuse.

Null only when nothing has pins that do the same job. Pin count alone is
never the test. A part that is not on a schematic at all — a bare board, an
enclosure, a host the board plugs into — is null.

`fit` comes back with the answer: `exact`, `family` or `generic`.

## Renaming a pin

`rename` carries the pins whose names differ from the datasheet — pin number
to the name it should carry. It is for a symbol that **is** the part and
names a pin its own way. It is not a way to reshape a symbol: if the pins are
not the part's pins, the answer is null.

## The origin

A copied symbol carries an `origin` property naming the library and symbol it
was taken from. `symbol-draw` writes it. The symbol belongs to the project
from the moment it is copied, and nothing else in it would say where it came
from.

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
