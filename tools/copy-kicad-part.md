# copy-kicad-part

Copy a KiCad symbol into the project library. Give it a part number or a
description; it writes the symbol and prints what it wrote, or `null`.

| Reads | Writes |
|---|---|
| `lib/kicad-lib-index.json`<br>the KiCad libraries | `lib/<nickname>.kicad_sym` |

```
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> <hint> [--ipn IPN] [--nickname N] [--lib DIR ...]
python3 tools/board-build/tools/copy-kicad-part.py <board-dir> --batch FILE [--lib DIR ...]
```

`--pins N` gives the part's pin count; a symbol with any other count is
refused, the numbers named. `--pinout FILE` gives the datasheet's pinout
— `[[number, name, type, side], ...]` from `datasheet-read` — and is the
better input: it sets the count, and the script renames every pin by
number to the datasheet's printed name, deterministically; the model only
chooses the symbol. A batch entry may carry `"pins"` or `"pinout"`. The
count is the symbol's style — an exposed pad counts as a pin.

Every run ends with a KPI line: parts, tokens, tokens per part. Elapsed
is the caller's to measure.

`<hint>` is a part number, a description, or both. `--ipn` names the copy;
without it the copy is named after the hint.

It does not read `board.db` and does not write it. `symbol-draw` calls it,
and `symbol-draw` owns the record.

Drawing a symbol is the last resort. This is asked first.

## Show, choose, take

No second session runs here — the LLM performing the stage is the one
already running (section 1.3 of the tool document). Two calls:

| Call | Does | Model |
|---|---|---|
| without `--take` | builds or loads the index through `lib-index`, scores the hint, prints the shortlist — library, symbol, pin count, description, pins — and stops | no |
| with `--take LIBRARY:SYMBOL` | copies the named symbol, renames every pin by number from `--pinout`, runs the gates, prints the library id | no |

The judgment — which symbol — happens between the two calls, in the
running session, in the open. The gates hold either way: the pin count
must match, a named pin must exist, and the choice may be any symbol in
the libraries, not only the shortlist.

## What counts as the part

A similar part's symbol is this part's symbol when its pins do the same job.
Take it, and rename the pins to the names the datasheet prints. The package
and the maker belong to the footprint, not the symbol, so a candidate in
another package is not a reason to refuse.

Null when nothing has pins that do the same job. Pin count alone is never
the test. A part that is not on a schematic at all — a bare board, an
enclosure, a host the board plugs into — is null.

`fit` comes back with the answer: `exact`, `family` or `generic`.

## What it writes

The symbol, under the name given, into `lib/<nickname>.kicad_sym`. Its
`Value` is that name, its `Footprint` is emptied — the package belongs to
`footprint-draw` — and an `origin` property names the library and symbol it
came from.

`init-pipeline` must have made the library first.

## What comes back is checked

The named symbol was one of the candidates, and every renamed pin is a pin
it has. A run that fails either stops and says so.

## What it refuses

- A project folder that does not exist, or with no `lib/<nickname>.kicad_sym`
- No `.kicad_pro` and no `--nickname`

An answer that fails validation — a library not on disk, a symbol the
library does not hold, a rename or unused entry naming a pin the symbol
does not have — becomes a null carrying the rejection reason, so the
caller's next resort still runs. It is printed, never silently dropped.

Each exits non-zero and names what it found.
