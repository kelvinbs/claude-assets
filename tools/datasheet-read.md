# datasheet-read

Read a pinout out of a datasheet. The first tool of process 2.

| Reads | Writes |
|---|---|
| `board.db` — `parts_table`<br>`datasheets/` | the pins, to `symbol-draw` or to standard output<br>`board.db` — `parts_table.datasheet` |

```
python3 tools/board-build/tools/datasheet-read.py <board-dir> <ipn> [options]
python3 tools/board-build/tools/datasheet-read.py <board-dir> --all [options]
```

Each read reports the tokens it spent (n9_1.33). It writes the part file when the part has none —
`design/parts/<IPN>-<name>.json` — the part file, its keys in T1. `symbol-draw` calls it and draws what comes
back; run alone it prints the pins. The pins belong in the symbol, and a
second copy of them beside the symbol is a second thing to keep true.

`--all` is every part whose `symbol` is null — the parts process 2 exists to
serve, per T2.2.

## There is no parser — there are two tiers

Datasheets do not share a layout, and a parser never converges. Tier 1
(n9_1.38): `pdftotext` extracts the pages that look like a pin table and
one plain text-in, JSON-out completion reads them — no tools, no page
rendering, seconds and a few thousand tokens. Tier 2, only when the text
yields nothing — a scan, a figure-only pinout: the full reader session
that renders pages and looks at them. The report names the tier.

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

The part's `mpn` column — the number the board was designed against,
per T2.6. The alternatives take the board as designed, so they take its
symbol, and no pinout is read for them.

## Finding the datasheet

Three places, in order:

| | |
|---|---|
| `--datasheet <path>` | given on the command line |
| `parts_table.datasheet` | recorded by an earlier run |
| `datasheets/` | tiered (n5.11), scanned recursively: whole part number in a filename, unique hit, decides; every partial prefix-run match is a lead only — the model sees the full listing plus leads, which sees the listing, may open files, and names the file or NONE — the choice is recorded |

Whatever it settles on is written back to `parts_table.datasheet`, relative to
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

## The part file

One file per part, `design/parts/<IPN>-<name>.json`. It holds what the
record does not: facts mined from the datasheet, and where a copy came
from. Nothing in it duplicates a `parts_table` column — the symbol and
footprint names live there. Keys add; the format never migrates; a key
absent is a fact not yet established, never a guess.

**T1 — Part file keys**

| # | Key | Holds | Stage |
|---|---|---|---|
| 1 | `pins` | `[[number, name, type, side], ...]`, as above | 3 |
| 2 | `units` | pin numbers per unit, `{"1": [..], "2": [..]}`. Multi-unit parts only | 3 |
| 3 | `symbol_donor` | `<library>:<symbol>` the symbol was copied from. `null` when drawn | 3 |
| 4 | `package` | the datasheet's package code | 5 |
| 5 | `package_dims` | body length, width, height, pitch, pad count, exposed-pad size — mm | 5 |
| 6 | `footprint_donor` | `<library>:<footprint>` the footprint was copied from. `null` when drawn | 5 |
| 7 | `pages` | datasheet page numbers each fact came from, `{"pins": [..], "package": [..]}` | 3, 5 |

A read writes `pins` and `pages.pins` and leaves every other key as it
found it.

The reader is given a scratch file to write, because that is how it is told
what to produce. The file is read back, checked, and deleted.

## Re-running

The part file is the cache: present, it is read; absent, the datasheet
is read and the file written. Delete the file to force a fresh read.

## What it refuses

- An IPN that does not read as one, or names no row
- A part with no `mpn` — the part number comes first
- A part number matching more than one file in `datasheets/`
- A part number matching none, with no `--datasheet`
- A path in `parts_table.datasheet` that is not on disk
- `--all` with `--datasheet`, which names one file and so names one part
- A project folder with no `board.db`, or one missing a table

Each exits non-zero and names what it found. With `--all`, one part failing
does not stop the rest; the run lists them at the end and exits non-zero.

## What it does not do

It does not open a KiCad file and it does not write `parts_table`. The
package and the physical fields belong to `footprint-draw`.
