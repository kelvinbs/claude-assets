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

## It reasons to the library, it does not grep

A library is not organised by order code, and there are hundreds of files. A
symbol may carry the family name with a trailing `x` for the package
letters, the die name, a package suffix the order code does not have, or a
generic name with the part number only in its description. Searching every
file for a string finds the easy half and misses the rest.

So the search is handed to `claude -p`, and the instruction is to work out
what the part *is* — an operational amplifier, a GaAs MMIC driver, an ARM
microcontroller, a SAW filter — and let that name the one or two libraries
that could hold it. It is given the list of library files and reads only
those. The near members of a family are judged too: they are how a library
is ruled out, and sometimes the same die is there under another name.

The instruction is the `PROMPT` string in `copy-kicad-part.py`, and that is
the thing to change when the search comes back wrong.

The instruction is explicit that a symbol for a different member of a
family, a part with the same pin count, or the same kind of part from
another maker is **not** this part, and that null is the right answer when
in doubt. A wrong symbol passes every check downstream and is found on the
bench.

What comes back is checked here against the library file: the symbol exists,
and every renamed pin is a pin it has.

## The primitives

A part with no specific symbol may still not need drawing. A resistor is
drawn as a resistor, a capacitor as a capacitor — an inductor, a diode, an
LED, a crystal, a test point, a jumper, a mounting hole, a coaxial
receptacle, the same. For those the generic symbol is the right symbol and
the part number is a field on it, not a different drawing.

That holds only where the generic drawing is the whole truth of the part. An
integrated circuit is not a generic anything, and a symbol whose pins are
not this part's pins is wrong however close the family.

A part number is not required. A resistor is drawn as a resistor before
anybody decides which one to buy, so a part with no row in `aml_table` is
searched on its description.

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
