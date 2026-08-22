# datasheet-read

Read a pinout out of a datasheet. The first tool of process 2.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`, `aml_table`, `mpn_table`<br>`datasheets/` | the pins, to `symbol-draw` or to standard output<br>`board.db` — `mpn_table.datasheet` |

```
python3 tools/board-build/tools/datasheet-read.py <board-dir> <ipn> [options]
python3 tools/board-build/tools/datasheet-read.py <board-dir> --all [options]
```

It writes no file of its own. `symbol-draw` calls it and draws what comes
back; run alone it prints the pins. The pins belong in the symbol, and a
second copy of them beside the symbol is a second thing to keep true.

`--all` is every part whose `symbol` is null — the parts process 2 exists to
serve, per T2.2.

## There is no parser

Datasheets do not share a layout. A table, a package drawing, a column
beside prose, a scan with no text in it. A parser is a new special case for
every part and never converges, so reading a pinout is looking at the page,
and the tool hands that job to `claude -p`.

The instruction it sends is the `PROMPT` string in `datasheet-read.py`. That
is the thing to change when a pinout comes out wrong. It says to render pages
and look at them rather than stop at the text layer, to take the variant the
part number names where a datasheet tabulates several, to go and find a
better datasheet when the one in the repository does not carry the pinout,
and to read the result back against the figure before finishing.

What comes back is checked here — every pin from 1 to N, none missing, none
repeated, a name on each, a real electrical type, a real side. A file that
does not hold up is deleted and the part is left without a pinout, so a bad
one never reaches a symbol.

Each part is one reader run. A full pass is slow, and it is not guaranteed
to come out identical twice. Run one IPN alone to see why one part fails.

## Which part number

The blank-rank row of `aml_table` — the MPN the board was designed against,
per T2.6. The alternatives take the board as designed, so they take its
symbol, and no pinout is read for them.

## Finding the datasheet

Three places, in order:

| | |
|---|---|
| `--datasheet <path>` | given on the command line |
| `mpn_table.datasheet` | recorded by an earlier run |
| `datasheets/` | tiered (n5.7), scanned recursively: whole part number in a filename, unique hit; else prefix-run scoring, unique maximum of 6+; a tie or zero-hit goes to the reader model, which sees the listing, may open files, and names the file or NONE — the choice is recorded |

Whatever it settles on is written back to `mpn_table.datasheet`, relative to
the repository so the record survives a clone. The second run needs no
argument.

The directory is the project's `datasheets/` if it has one, otherwise the
repository's. `--datasheets <dir>` names another.

A part number matching two files stops the run and lists them. Taking the
first is how a symbol gets drawn from the wrong part.

## What comes back

A pin is `[number, name, type, side]`. `side` is `L`, `R`, `T` or `B`.
`type` is a KiCad electrical type — `input`, `output`, `bidirectional`,
`tri_state`, `passive`, `free`, `unspecified`, `power_in`, `power_out`,
`open_collector`, `open_emitter`, `no_connect`. `~{NAME}` gives an overbar.
An exposed pad is a pin, numbered after the last numbered pin.

The reader is given a scratch file to write, because that is how it is told
what to produce. The file is read back, checked, and deleted.

## Re-running

Nothing is cached. `symbol-draw` skips a part that already has a symbol, so
a datasheet is read once per symbol drawn and not again.

## What it refuses

- An IPN that does not read as one, or names no row
- A part with no row in `aml_table` — the part number comes first
- A part number matching more than one file in `datasheets/`
- A part number matching none, with no `--datasheet`
- A path in `mpn_table.datasheet` that is not on disk
- `--all` with `--datasheet`, which names one file and so names one part
- A project folder with no `board.db`, or one missing a table

Each exits non-zero and names what it found. With `--all`, one part failing
does not stop the rest; the run lists them at the end and exits non-zero.

## What it does not do

It does not open a KiCad file and it does not write `parts_table`. The
package and the physical fields belong to `footprint-draw`.
